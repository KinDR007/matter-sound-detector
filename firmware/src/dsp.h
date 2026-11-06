#ifndef TSD_DSP_H
#define TSD_DSP_H

#include "model.h"

#ifdef __cplusplus
extern "C" {
#endif

/* One log-mel frame from MD_WIN samples (float, [-1, 1]). Matches model/train.py logmel(). */
void dsp_logmel_frame(const float *samples, float *mel_out);

/* Run the CNN on [MD_N_FRAMES][MD_N_MELS] log-mel features. */
void dsp_model_run(const float *feat, float *logits);

/* Int8 CNN (model_q8.c), called by dsp_model_run() with the model lock held. */
void model_q8_run(const float *feat, float *logits);

/* Compare firmware output with the Python reference; returns max abs logit diff.
 * Int8: the float front end may flip a rare input rounding, hence the looser limit. */
#ifdef CONFIG_TSD_MODEL_INT8
#define DSP_SELFTEST_TOL 5e-2f
#else
#define DSP_SELFTEST_TOL 1e-2f
#endif
float dsp_selftest(void);

#ifdef __cplusplus
}
#endif

#endif
