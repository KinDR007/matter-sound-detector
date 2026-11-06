/*
 * Log-mel features and student CNN inference, mirrors model/features.py and
 * model/train_student.py:
 *   Hann(WIN) -> zero pad N_FFT -> |rfft|^2 -> mel filterbank -> ln(x + eps)
 *   normalize -> conv3x3 + ReLU + maxpool2
 *   -> blocks [dw3x3 + ReLU -> pw1x1 + ReLU (-> maxpool2)] -> GAP -> FC
 * Float model: tensors CHW, weights in PyTorch layout with BatchNorm folded.
 * Int8 model (CONFIG_TSD_MODEL_INT8): model_q8.c.
 */

#include <math.h>
#include <string.h>
#include <stdbool.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/util.h>
#include <arm_math.h>

#include "dsp.h"

#define N_BINS (MD_N_FFT / 2 + 1)
#define H0 MD_N_FRAMES
#define W0 MD_N_MELS

/* Static work buffers: FFT ones (fft_lock) and CNN ones (model_lock). */
static K_MUTEX_DEFINE(fft_lock);
static K_MUTEX_DEFINE(model_lock);

static float window[MD_WIN];
static bool window_ready;
static arm_rfft_fast_instance_f32 rfft;
static float fft_in[MD_N_FFT];
static float fft_out[MD_N_FFT];
static float power[N_BINS];

static void dsp_init(void)
{
	for (int i = 0; i < MD_WIN; i++) {
		window[i] = 0.5f - 0.5f * cosf(2.0f * (float)PI * i / MD_WIN);
	}
	arm_rfft_fast_init_f32(&rfft, MD_N_FFT);
	window_ready = true;
}

static void logmel_frame(const float *samples, float *mel_out)
{
	if (!window_ready) {
		dsp_init();
	}
	for (int i = 0; i < MD_WIN; i++) {
		fft_in[i] = samples[i] * window[i];
	}
	memset(&fft_in[MD_WIN], 0, (MD_N_FFT - MD_WIN) * sizeof(float));

	arm_rfft_fast_f32(&rfft, fft_in, fft_out, 0);

	/* CMSIS packed format: [re0, re(N/2), re1, im1, re2, im2, ...] */
	power[0] = fft_out[0] * fft_out[0];
	power[N_BINS - 1] = fft_out[1] * fft_out[1];
	for (int k = 1; k < N_BINS - 1; k++) {
		float re = fft_out[2 * k];
		float im = fft_out[2 * k + 1];

		power[k] = re * re + im * im;
	}
	for (int m = 0; m < MD_N_MELS; m++) {
		const float *fb = &md_mel_fb[m * N_BINS];
		float acc = 0.0f;

		for (int k = 0; k < N_BINS; k++) {
			acc += fb[k] * power[k];
		}
		mel_out[m] = logf(acc + MD_LOG_EPS);
	}
}

#ifndef CONFIG_TSD_MODEL_INT8
/* three rotating activation buffers: block input, depthwise output, block output */
static float buf[3][MD_MAX_ACT];
static float padded[(H0 + 2) * (W0 + 2)];
static float plane[H0 * W0];

/* 3x3 convolution of one zero-padded input plane, accumulated into plane[]. */
static void conv_plane_acc(const float *src_padded, int h, int w, const float *k)
{
	const int pw = w + 2;

	for (int ky = 0; ky < 3; ky++) {
		for (int kx = 0; kx < 3; kx++) {
			const float kv = k[ky * 3 + kx];

			for (int y = 0; y < h; y++) {
				const float *s = &src_padded[(y + ky) * pw + kx];
				float *d = &plane[y * w];

				for (int x = 0; x < w; x++) {
					d[x] += kv * s[x];
				}
			}
		}
	}
}

static void pad_plane(const float *in, int h, int w)
{
	const int pw = w + 2;

	memset(padded, 0, (h + 2) * pw * sizeof(float));
	for (int y = 0; y < h; y++) {
		memcpy(&padded[(y + 1) * pw + 1], &in[y * w], w * sizeof(float));
	}
}

/* ReLU and optional 2x2 max pooling (floor) of plane[] into out. */
static void relu_pool_store(int h, int w, bool pool, float *out)
{
	if (!pool) {
		for (int i = 0; i < h * w; i++) {
			out[i] = MAX(plane[i], 0.0f);
		}
		return;
	}
	const int oh = h / 2, ow = w / 2;

	for (int oy = 0; oy < oh; oy++) {
		for (int ox = 0; ox < ow; ox++) {
			const float *p = &plane[(2 * oy) * w + 2 * ox];
			float m = MAX(MAX(p[0], p[1]), MAX(p[w], p[w + 1]));

			out[oy * ow + ox] = MAX(m, 0.0f);
		}
	}
}

/* Input conv: 1 -> MD_C0 channels, ReLU, maxpool. */
static void conv0(const float *in, float *out)
{
	pad_plane(in, H0, W0);
	for (int co = 0; co < MD_C0; co++) {
		for (int i = 0; i < H0 * W0; i++) {
			plane[i] = md_c0_b[co];
		}
		conv_plane_acc(padded, H0, W0, &md_c0_w[co * 9]);
		relu_pool_store(H0, W0, true, &out[co * (H0 / 2) * (W0 / 2)]);
	}
}

/* Depthwise 3x3 + ReLU (in -> tmp), pointwise 1x1 + ReLU (+pool) (tmp -> out). */
static void ds_block(const struct md_block *b, const float *in, float *tmp, float *out, int h,
		     int w)
{
	const int hw = h * w;

	for (int c = 0; c < b->cin; c++) {
		pad_plane(&in[c * hw], h, w);
		for (int i = 0; i < hw; i++) {
			plane[i] = b->dw_b[c];
		}
		conv_plane_acc(padded, h, w, &b->dw_w[c * 9]);
		relu_pool_store(h, w, false, &tmp[c * hw]);
	}

	const int ohw = b->pool ? (h / 2) * (w / 2) : hw;

	for (int co = 0; co < b->cout; co++) {
		const float *wt = &b->pw_w[co * b->cin];

		for (int i = 0; i < hw; i++) {
			plane[i] = b->pw_b[co];
		}
		for (int ci = 0; ci < b->cin; ci++) {
			const float kv = wt[ci];
			const float *s = &tmp[ci * hw];

			for (int i = 0; i < hw; i++) {
				plane[i] += kv * s[i];
			}
		}
		relu_pool_store(h, w, b->pool, &out[co * ohw]);
	}
}

static void model_run(const float *feat, float *logits)
{
	int h = H0 / 2, w = W0 / 2;
	float *in = buf[0], *tmp = buf[1], *out = buf[2];

	/* normalized input in buf[1], conv0 output in buf[0] */
	for (int i = 0; i < H0 * W0; i++) {
		tmp[i] = (feat[i] - MD_NORM_MEAN) / MD_NORM_STD;
	}
	conv0(tmp, in);

	for (int i = 0; i < MD_N_BLOCKS; i++) {
		const struct md_block *b = &md_blocks[i];
		float *t;

		ds_block(b, in, tmp, out, h, w);
		t = in;
		in = out;
		out = tmp;
		tmp = t;
		if (b->pool) {
			h /= 2;
			w /= 2;
		}
	}
	const float *cur = in;

	float gap[MD_C_LAST];

	for (int c = 0; c < MD_C_LAST; c++) {
		float s = 0.0f;

		for (int i = 0; i < h * w; i++) {
			s += cur[c * h * w + i];
		}
		gap[c] = s / (h * w);
	}
	for (int o = 0; o < MD_N_CLASSES; o++) {
		float s = md_fc_b[o];

		for (int c = 0; c < MD_C_LAST; c++) {
			s += md_fc_w[o * MD_C_LAST + c] * gap[c];
		}
		logits[o] = s;
	}
}
#endif /* !CONFIG_TSD_MODEL_INT8 */

void dsp_logmel_frame(const float *samples, float *mel_out)
{
	k_mutex_lock(&fft_lock, K_FOREVER);
	logmel_frame(samples, mel_out);
	k_mutex_unlock(&fft_lock);
}

void dsp_model_run(const float *feat, float *logits)
{
	k_mutex_lock(&model_lock, K_FOREVER);
#ifdef CONFIG_TSD_MODEL_INT8
	model_q8_run(feat, logits);
#else
	model_run(feat, logits);
#endif
	k_mutex_unlock(&model_lock);
}

float dsp_selftest(void)
{
	/* Same chirp as model/features.py test_signal(), generated frame by frame */
	static float frame[MD_WIN];
	static float feat[MD_N_FRAMES * MD_N_MELS];
	const int n = MD_WIN + MD_HOP * (MD_N_FRAMES - 1);
	const double f0 = 300.0, f1 = 4000.0;
	const double k = (f1 - f0) / ((double)n / MD_SR);
	float logits[MD_N_CLASSES];
	float max_diff = 0.0f;

	for (int f = 0; f < MD_N_FRAMES; f++) {
		for (int i = 0; i < MD_WIN; i++) {
			double t = (double)(f * MD_HOP + i) / MD_SR;

			frame[i] = (float)(0.3 * sin(2.0 * 3.14159265358979323846 *
						     (f0 * t + 0.5 * k * t * t)));
		}
		dsp_logmel_frame(frame, &feat[f * MD_N_MELS]);
	}
	dsp_model_run(feat, logits);
	for (int i = 0; i < MD_N_CLASSES; i++) {
		max_diff = MAX(max_diff, fabsf(logits[i] - md_test_logits[i]));
	}
	return max_diff;
}
