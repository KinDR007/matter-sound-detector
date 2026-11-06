#ifndef TSD_AUDIO_H
#define TSD_AUDIO_H

#include <stdint.h>
#include "model.h"

#ifdef __cplusplus
extern "C" {
#endif

struct audio_result {
	float prob[MD_N_CLASSES];
	float level_dbfs; /* RMS level of the classified 1 s window */
	uint32_t infer_ms;
};

typedef void (*audio_result_cb_t)(const struct audio_result *res);

/* Starts PDM capture, feature extraction and periodic classification. */
int audio_start(audio_result_cb_t cb);

#ifdef __cplusplus
}
#endif

#endif
