/*
 * Turns per-window class probabilities into discrete sound events:
 * - class probability >= per-class threshold (exported with the model)
 * - in md_confirm[class] consecutive windows (1 or 2, exported with the model;
 *   windows overlap by 0.5 s)
 * - input level above LEVEL_GATE_DBFS (mic noise floor is ~ -70 dBFS with +20 dB PDM gain)
 * - at most one event per class every COOLDOWN_MS
 * - alarm family: a generic class (alarm, beep) does not fire while a specific
 *   one (smoke_alarm, alarm_clock, phone_ring) is above its threshold or fired
 *   within COOLDOWN_MS; a smoke alarm is also an alarm and a beep, one event is
 *   enough (validation: generic+specific false events -16 %, specific classes
 *   and family-level recall unchanged)
 */

#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include "detector.h"

LOG_MODULE_REGISTER(detector, LOG_LEVEL_INF);

#define LEVEL_GATE_DBFS (-64.0f)
#define COOLDOWN_MS 5000

static detector_event_cb_t event_cb;
static bool verbose;
static uint8_t hits[MD_N_CLASSES];
static int64_t last_event[MD_N_CLASSES];
static int64_t last_specific;

static const char *const specific_names[] = {"smoke_alarm", "alarm_clock", "phone_ring"};
static const char *const generic_names[] = {"alarm", "beep"};
static bool is_specific[MD_N_CLASSES];
static bool is_generic[MD_N_CLASSES];

static void mark(const char *const *names, size_t n, bool *flags)
{
	for (size_t i = 0; i < n; i++) {
		for (int c = 0; c < MD_N_CLASSES; c++) {
			if (strcmp(md_class_names[c], names[i]) == 0) {
				flags[c] = true;
			}
		}
	}
}

void detector_init(detector_event_cb_t cb)
{
	event_cb = cb;
	for (int i = 0; i < MD_N_CLASSES; i++) {
		last_event[i] = -COOLDOWN_MS;
	}
	last_specific = -COOLDOWN_MS;
	mark(specific_names, ARRAY_SIZE(specific_names), is_specific);
	mark(generic_names, ARRAY_SIZE(generic_names), is_generic);
}

void detector_set_verbose(bool on)
{
	verbose = on;
}

void detector_feed(const struct audio_result *res)
{
	const bool loud = res->level_dbfs >= LEVEL_GATE_DBFS;
	const int64_t now = k_uptime_get();
	int best = 0;
	bool specific_active = now - last_specific < COOLDOWN_MS;

	for (int c = 0; c < MD_N_CLASSES; c++) {
		if (is_specific[c] && loud && res->prob[c] >= md_thresholds[c]) {
			specific_active = true;
		}
	}

	for (int c = 0; c < MD_N_CLASSES; c++) {
		if (res->prob[c] > res->prob[best]) {
			best = c;
		}
		if (!loud || res->prob[c] < md_thresholds[c]) {
			hits[c] = 0;
			continue;
		}
		if (hits[c] < md_confirm[c]) {
			hits[c]++;
		}
		if (hits[c] == md_confirm[c] && now - last_event[c] >= COOLDOWN_MS &&
		    !(is_generic[c] && specific_active)) {
			last_event[c] = now;
			if (is_specific[c]) {
				last_specific = now;
			}
			LOG_INF("EVENT %s p=%d%% level %d dBFS", md_class_names[c],
				(int)(res->prob[c] * 100), (int)res->level_dbfs);
			if (event_cb) {
				event_cb(c, res->prob[c]);
			}
		}
	}

	if (verbose) {
		LOG_INF("top %-12s %3d%% (thr %d%%) level %d dBFS infer %u ms",
			md_class_names[best], (int)(res->prob[best] * 100),
			(int)(md_thresholds[best] * 100), (int)res->level_dbfs, res->infer_ms);
	}
}
