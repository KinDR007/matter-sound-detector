/*
 * PDM capture (16 kHz mono) -> log-mel frames (hop 20 ms) -> 1 s window
 * classified every 0.5 s in a separate lower-priority thread.
 */

#include <math.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/audio/dmic.h>
#include <zephyr/logging/log.h>
#include <hal/nrf_pdm.h>

#include "audio.h"
#include "dsp.h"

LOG_MODULE_REGISTER(audio, LOG_LEVEL_INF);

#define BLOCK_SAMPLES (MD_SR / 10) /* 100 ms */
#define BLOCK_BYTES   (BLOCK_SAMPLES * sizeof(int16_t))
#define BLOCK_COUNT   16 /* 1.6 s of slack when the CPU is busy */
#define SETTLE_BLOCKS 2  /* blocks dropped after a PDM (re)start: filter start-up transient */
#define READ_TIMEOUT  1000

#define INFER_EVERY_FRAMES (MD_N_FRAMES / 2) /* ~0.5 s */
#define LEVEL_BLOCKS 10                      /* 1 s of block energies */

K_MEM_SLAB_DEFINE_STATIC(pcm_slab, BLOCK_BYTES, BLOCK_COUNT, 4);

static audio_result_cb_t result_cb;

/* sample accumulator for framing */
static float pending[MD_WIN + BLOCK_SAMPLES + MD_HOP];
static size_t pending_len;

/* ring of log-mel frames */
static float mel_ring[MD_N_FRAMES][MD_N_MELS];
static size_t mel_head;
static size_t mel_count;
static size_t frames_since_infer;

static float block_ms[LEVEL_BLOCKS];
static size_t block_idx;

/* handed over to the inference thread */
static float infer_feat[MD_N_FRAMES * MD_N_MELS];
static float infer_level;
static K_SEM_DEFINE(infer_sem, 0, 1);
static atomic_t infer_busy;

static void queue_inference(void)
{
	if (atomic_get(&infer_busy)) {
		LOG_DBG("inference still running, window skipped");
		return;
	}
	for (size_t i = 0; i < MD_N_FRAMES; i++) {
		size_t src = (mel_head + i) % MD_N_FRAMES; /* oldest first */

		memcpy(&infer_feat[i * MD_N_MELS], mel_ring[src], sizeof(mel_ring[0]));
	}

	float ms = 0.0f;

	for (size_t i = 0; i < LEVEL_BLOCKS; i++) {
		ms += block_ms[i];
	}
	infer_level = 10.0f * log10f(ms / LEVEL_BLOCKS + 1e-12f);

	atomic_set(&infer_busy, 1);
	k_sem_give(&infer_sem);
}

static void process_block(const int16_t *pcm)
{
	float ms = 0.0f;

	for (size_t i = 0; i < BLOCK_SAMPLES; i++) {
		float v = pcm[i] / 32768.0f;

		pending[pending_len + i] = v;
		ms += v * v;
	}
	pending_len += BLOCK_SAMPLES;
	block_ms[block_idx] = ms / BLOCK_SAMPLES;
	block_idx = (block_idx + 1) % LEVEL_BLOCKS;

	size_t pos = 0;

	while (pending_len - pos >= MD_WIN) {
		dsp_logmel_frame(&pending[pos], mel_ring[mel_head]);
		mel_head = (mel_head + 1) % MD_N_FRAMES;
		if (mel_count < MD_N_FRAMES) {
			mel_count++;
		}
		pos += MD_HOP;

		if (++frames_since_infer >= INFER_EVERY_FRAMES && mel_count == MD_N_FRAMES) {
			frames_since_infer = 0;
			queue_inference();
		}
	}
	memmove(pending, &pending[pos], (pending_len - pos) * sizeof(float));
	pending_len -= pos;
}

static void capture_thread(void *a, void *b, void *c)
{
	const struct device *dmic = DEVICE_DT_GET(DT_NODELABEL(dmic_dev));
	struct pcm_stream_cfg stream = {
		.pcm_width = 16,
		.mem_slab = &pcm_slab,
		.pcm_rate = MD_SR,
		.block_size = BLOCK_BYTES,
	};
	struct dmic_cfg cfg = {
		.io = {
			.min_pdm_clk_freq = 1000000,
			.max_pdm_clk_freq = 3500000,
			.min_pdm_clk_dc = 40,
			.max_pdm_clk_dc = 60,
		},
		.streams = &stream,
		.channel = {
			.req_num_streams = 1,
			.req_num_chan = 1,
		},
	};
	int ret;

	cfg.channel.req_chan_map_lo = dmic_build_channel_map(0, 0, PDM_CHAN_LEFT);

	if (!device_is_ready(dmic)) {
		LOG_ERR("PDM device not ready");
		return;
	}
	ret = dmic_configure(dmic, &cfg);
	if (ret == 0) {
		/* Driver keeps the default 0 dB; MEMS mic level is low, use +20 dB */
		nrf_pdm_gain_set(NRF_PDM20, NRF_PDM_GAIN_MAXIMUM, NRF_PDM_GAIN_MAXIMUM);
		ret = dmic_trigger(dmic, DMIC_TRIGGER_START);
	}
	if (ret) {
		LOG_ERR("PDM start failed (%d)", ret);
		return;
	}
	LOG_INF("PDM capture %d Hz", MD_SR);
	int settle = SETTLE_BLOCKS;

	for (;;) {
		void *buf;
		uint32_t size;

		ret = dmic_read(dmic, 0, &buf, &size, READ_TIMEOUT);
		if (ret) {
			LOG_ERR("PDM read failed (%d), restarting", ret);
			dmic_trigger(dmic, DMIC_TRIGGER_STOP);
			k_msleep(100);
			dmic_trigger(dmic, DMIC_TRIGGER_START);
			/* Start over: the gap and the start-up transient must not reach the
			 * model (they looked like beeps). Inference resumes once 1 s of
			 * new audio has filled the mel ring. */
			pending_len = 0;
			mel_count = 0;
			frames_since_infer = 0;
			settle = SETTLE_BLOCKS;
			continue;
		}
		if (size == BLOCK_BYTES && settle > 0) {
			settle--;
		} else if (size == BLOCK_BYTES) {
			process_block(buf);
		}
		k_mem_slab_free(&pcm_slab, buf);
	}
}

static void infer_thread(void *a, void *b, void *c)
{
	for (;;) {
		struct audio_result res;
		float logits[MD_N_CLASSES];

		k_sem_take(&infer_sem, K_FOREVER);
		int64_t t0 = k_uptime_get();

		dsp_model_run(infer_feat, logits);
		res.infer_ms = (uint32_t)(k_uptime_get() - t0);
		res.level_dbfs = infer_level;

		/* multi-label: independent sigmoid per class */
		for (int i = 0; i < MD_N_CLASSES; i++) {
			res.prob[i] = 1.0f / (1.0f + expf(-logits[i]));
		}
		atomic_set(&infer_busy, 0);

		if (result_cb) {
			result_cb(&res);
		}
	}
}

/* Capture above the Matter/OpenThread threads (priority 8 and lower): it only
 * needs a few ms per 100 ms block, but must never fall behind the PDM DMA. */
K_THREAD_DEFINE(capture_tid, 4096, capture_thread, NULL, NULL, NULL, 2, 0, K_TICKS_FOREVER);
K_THREAD_DEFINE(infer_tid, 4096, infer_thread, NULL, NULL, NULL, 10, 0, K_TICKS_FOREVER);

int audio_start(audio_result_cb_t cb)
{
	result_cb = cb;
	k_thread_start(infer_tid);
	k_thread_start(capture_tid);
	return 0;
}
