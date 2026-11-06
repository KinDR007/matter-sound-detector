#ifndef TSD_DETECTOR_H
#define TSD_DETECTOR_H

#include <stdbool.h>
#include "audio.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Called (from the inference thread) when a sound event is confirmed. */
typedef void (*detector_event_cb_t)(int cls, float prob);

void detector_init(detector_event_cb_t cb);

/* Feed one classification result (every ~0.5 s). */
void detector_feed(const struct audio_result *res);

void detector_set_verbose(bool on);

#ifdef __cplusplus
}
#endif

#endif
