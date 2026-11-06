/*
 * Int8 student CNN with CMSIS-NN, mirrors model/quantize.py QModel:
 *   quantize input -> conv3x3 + ReLU + maxpool2
 *   -> blocks [dw3x3 + ReLU -> pw1x1 + ReLU (-> maxpool2)] -> GAP (float) -> FC (float)
 * Tensors are NHWC int8. Layers followed by pooling run on two-row strips so
 * the full-resolution output never has to be stored.
 */

#include <math.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <arm_nnfunctions.h>

#include "dsp.h"

LOG_MODULE_REGISTER(model_q8, LOG_LEVEL_INF);

#define H0 MD_N_FRAMES
#define W0 MD_N_MELS
#define SCRATCH_SIZE 4096

static int8_t act[3][MD_Q_MAX_ACT];
static int8_t in_pad[(H0 + 2) * (W0 + 2)];
static int8_t strip[MD_Q_STRIP];
static int8_t scratch[SCRATCH_SIZE] __aligned(4);
static bool failed;

static void check(arm_cmsis_nn_status st, const char *what)
{
	if (st != ARM_CMSIS_NN_SUCCESS && !failed) {
		failed = true;
		LOG_ERR("%s failed (%d)", what, st);
	}
}

static cmsis_nn_context ctx(int32_t need)
{
	if (need > SCRATCH_SIZE && !failed) {
		failed = true;
		LOG_ERR("CMSIS-NN scratch %d > %d", need, SCRATCH_SIZE);
	}
	return (cmsis_nn_context){.buf = scratch, .size = SCRATCH_SIZE};
}

/* 2x2 max pooling of a two-row strip {1, 2, w, c} into one row. */
static void pool_strip(const int8_t *src, int w, int c, int8_t *dst)
{
	const cmsis_nn_pool_params pp = {.stride = {2, 2}, .padding = {0, 0},
					 .activation = {-128, 127}};
	const cmsis_nn_dims in = {1, 2, w, c}, flt = {1, 2, 2, 1}, out = {1, 1, w / 2, c};
	cmsis_nn_context c0 = {.buf = NULL, .size = 0};

	check(arm_max_pool_s8(&c0, &pp, &in, src, &flt, &out, dst), "max_pool");
}

/* 3x3 conv 1 -> MD_C0, ReLU, 2x2 max pooling; out {1, H0/2, W0/2, MD_C0}. */
static void conv0(int8_t *out)
{
	const cmsis_nn_conv_params cp = {
		.input_offset = -MD_Q_IN_ZP, .output_offset = MD_Q_C0_OUT_ZP,
		.stride = {1, 1}, .padding = {0, 0}, .dilation = {1, 1},
		.activation = {MD_Q_C0_OUT_ZP, 127}};
	const cmsis_nn_per_channel_quant_params qp = {(int32_t *)md_q_c0_m, (int32_t *)md_q_c0_s};
	const cmsis_nn_dims in = {1, 4, W0 + 2, 1}, flt = {MD_C0, 3, 3, 1}, bias = {1, 1, 1, MD_C0};
	const cmsis_nn_dims o = {1, 2, W0, MD_C0};
	cmsis_nn_context c = ctx(arm_convolve_wrapper_s8_get_buffer_size(&cp, &in, &flt, &o));

	/* input already zero padded (pad value = zero point = real 0) */
	for (int oy = 0; oy < H0 / 2; oy++) {
		check(arm_convolve_wrapper_s8(&c, &cp, &qp, &in, &in_pad[2 * oy * (W0 + 2)], &flt,
					      md_q_c0_w, &bias, md_q_c0_b, &o, strip),
		      "conv0");
		pool_strip(strip, W0, MD_C0, &out[oy * (W0 / 2) * MD_C0]);
	}
}

/* Depthwise 3x3 + ReLU (in -> tmp), pointwise 1x1 + ReLU (+pool) (tmp -> out). */
static void ds_block(const struct md_qblock *b, int in_zp, const int8_t *in, int8_t *tmp, int8_t *out,
		     int h, int w)
{
	const cmsis_nn_dw_conv_params dp = {
		.input_offset = -in_zp, .output_offset = b->dw_out_zp, .ch_mult = 1,
		.stride = {1, 1}, .padding = {1, 1}, .dilation = {1, 1},
		.activation = {b->dw_out_zp, 127}};
	const cmsis_nn_per_channel_quant_params dq = {(int32_t *)b->dw_m, (int32_t *)b->dw_s};
	const cmsis_nn_dims din = {1, h, w, b->cin}, dflt = {1, 3, 3, b->cin};
	const cmsis_nn_dims dbias = {1, 1, 1, b->cin}, dout = {1, h, w, b->cin};
	cmsis_nn_context c = ctx(arm_depthwise_conv_wrapper_s8_get_buffer_size(&dp, &din, &dflt, &dout));

	check(arm_depthwise_conv_wrapper_s8(&c, &dp, &dq, &din, in, &dflt, b->dw_w, &dbias, b->dw_b,
					    &dout, tmp),
	      "dw_conv");

	const cmsis_nn_conv_params pp = {
		.input_offset = -b->dw_out_zp, .output_offset = b->pw_out_zp,
		.stride = {1, 1}, .padding = {0, 0}, .dilation = {1, 1},
		.activation = {b->pw_out_zp, 127}};
	const cmsis_nn_per_channel_quant_params pq = {(int32_t *)b->pw_m, (int32_t *)b->pw_s};
	const cmsis_nn_dims pflt = {b->cout, 1, 1, b->cin}, pbias = {1, 1, 1, b->cout};

	if (!b->pool) {
		const cmsis_nn_dims pin = {1, h, w, b->cin}, pout = {1, h, w, b->cout};

		c = ctx(arm_convolve_wrapper_s8_get_buffer_size(&pp, &pin, &pflt, &pout));
		check(arm_convolve_wrapper_s8(&c, &pp, &pq, &pin, tmp, &pflt, b->pw_w, &pbias, b->pw_b,
					      &pout, out),
		      "pw_conv");
		return;
	}
	const cmsis_nn_dims pin = {1, 2, w, b->cin}, pout = {1, 2, w, b->cout};

	c = ctx(arm_convolve_wrapper_s8_get_buffer_size(&pp, &pin, &pflt, &pout));
	for (int oy = 0; oy < h / 2; oy++) {
		check(arm_convolve_wrapper_s8(&c, &pp, &pq, &pin, &tmp[2 * oy * w * b->cin], &pflt,
					      b->pw_w, &pbias, b->pw_b, &pout, strip),
		      "pw_conv");
		pool_strip(strip, w, b->cout, &out[oy * (w / 2) * b->cout]);
	}
}

void model_q8_run(const float *feat, float *logits)
{
	int h = H0 / 2, w = W0 / 2;
	int8_t *in = act[0], *tmp = act[1], *out = act[2];
	int zp = MD_Q_C0_OUT_ZP;

	/* normalise + quantise into the zero padded input (pad = zero point) */
	memset(in_pad, MD_Q_IN_ZP, sizeof(in_pad));
	for (int y = 0; y < H0; y++) {
		for (int x = 0; x < W0; x++) {
			float v = (feat[y * W0 + x] - MD_NORM_MEAN) / MD_NORM_STD;
			float q = floorf(v / MD_Q_IN_SCALE + 0.5f) + MD_Q_IN_ZP;

			in_pad[(y + 1) * (W0 + 2) + x + 1] = (int8_t)CLAMP(q, -128.0f, 127.0f);
		}
	}
	conv0(in);

	for (int i = 0; i < MD_N_BLOCKS; i++) {
		const struct md_qblock *b = &md_qblocks[i];
		int8_t *t;

		ds_block(b, zp, in, tmp, out, h, w);
		zp = b->pw_out_zp;
		t = in;
		in = out;
		out = tmp;
		tmp = t;
		if (b->pool) {
			h /= 2;
			w /= 2;
		}
	}

	static float gap[MD_C_LAST]; /* model lock held */

	memset(gap, 0, sizeof(gap));
	for (int p = 0; p < h * w; p++) {
		for (int c = 0; c < MD_C_LAST; c++) {
			gap[c] += (float)(in[p * MD_C_LAST + c] - zp);
		}
	}
	for (int c = 0; c < MD_C_LAST; c++) {
		gap[c] *= MD_Q_LAST_SCALE / (h * w);
	}
	for (int o = 0; o < MD_N_CLASSES; o++) {
		float s = md_fc_b[o];

		for (int c = 0; c < MD_C_LAST; c++) {
			s += md_fc_w[o * MD_C_LAST + c] * gap[c];
		}
		logits[o] = s;
	}
}
