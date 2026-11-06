/*
 * Thread sound detector, Matter over Thread.
 *
 * Every sound class is a Generic Switch endpoint (1..N). A confirmed sound
 * event emits Switch InitialPress on that endpoint, which Home Assistant shows
 * as an event entity named after the class (Fixed Label "ha_entitylabel").
 *
 * Init structure based on the nRF Connect SDK Matter template sample,
 * Copyright (c) 2021 Nordic Semiconductor ASA
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include "app_task.h"

#include "app/matter_init.h"
#include "app/task_executor.h"
#include "board/board.h"
#include "clusters/identify.h"
#include "lib/core/CHIPError.h"
#include "lib/support/CodeUtils.h"

#include <app/clusters/switch-server/switch-server.h>
#include <platform/CHIPDeviceLayer.h>
#include <setup_payload/OnboardingCodesUtil.h>

#include <zephyr/logging/log.h>
#include <zephyr/shell/shell.h>

#include <cstdlib>
#include <cstring>
#include <new>

#include "audio.h"
#include "detector.h"
#include "device_info.h"
#include "dsp.h"
#include "label_provider.h"

LOG_MODULE_DECLARE(app, CONFIG_CHIP_APP_LOG_LEVEL);

using namespace ::chip;
using namespace ::chip::app;
using namespace ::chip::DeviceLayer;

namespace {

SoundLabelProvider sLabelProvider;

/* Identify cluster on every sound endpoint (required by the Generic Switch device type). No LED or
 * sound to show, so identify requests are only logged. */
class LogIdentifyDelegate : public Clusters::IdentifyDelegate {
public:
	void OnIdentifyStart(Clusters::IdentifyCluster &) override { LOG_INF("Identify start"); }
	void OnIdentifyStop(Clusters::IdentifyCluster &) override { LOG_INF("Identify stop"); }
	void OnTriggerEffect(Clusters::IdentifyCluster &) override { LOG_INF("Identify trigger effect"); }
	bool IsTriggerEffectEnabled() const override { return false; }
};

LogIdentifyDelegate sIdentifyDelegate;
DefaultTimerDelegate sIdentifyTimer;
alignas(Nrf::Matter::IdentifyCluster) uint8_t sIdentifyStorage[MD_N_CLASSES][sizeof(Nrf::Matter::IdentifyCluster)];

CHIP_ERROR InitIdentify()
{
	for (int i = 0; i < MD_N_CLASSES; i++) {
		auto *cluster = new (sIdentifyStorage[i]) Nrf::Matter::IdentifyCluster(
			SoundLabelProvider::kFirstEndpoint + i, sIdentifyDelegate, sIdentifyTimer,
			Clusters::Identify::IdentifyTypeEnum::kNone);

		ReturnErrorOnFailure(cluster->Init());
	}
	return CHIP_NO_ERROR;
}

/* Runs on the Matter thread. */
void EmitSoundEvent(intptr_t cls)
{
	EndpointId ep = SoundLabelProvider::kFirstEndpoint + static_cast<EndpointId>(cls);

	Clusters::SwitchServer::Instance().OnInitialPress(ep, 1);
	LOG_INF("Matter event: %s on endpoint %u", SoundLabelProvider::PrettyName(cls), ep);
}

/* Called from the inference thread. */
void OnSoundEvent(int cls, float prob)
{
	ARG_UNUSED(prob);
	PlatformMgr().ScheduleWork(EmitSoundEvent, static_cast<intptr_t>(cls));
}

void OnAudioResult(const struct audio_result *res)
{
	detector_feed(res);
}

int CmdSelftest(const struct shell *sh, size_t argc, char **argv)
{
	int64_t t0 = k_uptime_get();
	float diff = dsp_selftest();

	shell_print(sh, "selftest: max |logit diff| vs Python = %.6f (%s), %lld ms", (double)diff,
		    diff < DSP_SELFTEST_TOL ? "PASS" : "FAIL", k_uptime_get() - t0);
	return 0;
}

int CmdVerbose(const struct shell *sh, size_t argc, char **argv)
{
	bool on = argv[1][0] == '1';

	detector_set_verbose(on);
	shell_print(sh, "verbose %d", on);
	return 0;
}

int CmdClasses(const struct shell *sh, size_t argc, char **argv)
{
	for (int i = 0; i < MD_N_CLASSES; i++) {
		shell_print(sh, "%2d endpoint %2d %-12s threshold %d%%", i,
			    SoundLabelProvider::kFirstEndpoint + i, md_class_names[i],
			    (int)(md_thresholds[i] * 100));
	}
	return 0;
}

/* Fire a Matter event without a sound, to test the Home Assistant side. */
int CmdTest(const struct shell *sh, size_t argc, char **argv)
{
	int cls = -1;

	for (int i = 0; i < MD_N_CLASSES; i++) {
		if (!strcmp(argv[1], md_class_names[i])) {
			cls = i;
		}
	}
	if (cls < 0) {
		char *end;
		long v = strtol(argv[1], &end, 10);

		if (*end == '\0' && v >= 0 && v < MD_N_CLASSES) {
			cls = (int)v;
		}
	}
	if (cls < 0) {
		shell_error(sh, "unknown class '%s' (see: snd classes)", argv[1]);
		return -EINVAL;
	}
	OnSoundEvent(cls, 1.0f);
	shell_print(sh, "test event %s queued", md_class_names[cls]);
	return 0;
}

SHELL_STATIC_SUBCMD_SET_CREATE(
	snd_cmds,
	SHELL_CMD(selftest, NULL, "Compare DSP+model output with the Python reference", CmdSelftest),
	SHELL_CMD_ARG(verbose, NULL, "verbose 0|1: log every classification", CmdVerbose, 2, 0),
	SHELL_CMD(classes, NULL, "List sound classes, endpoints and thresholds", CmdClasses),
	SHELL_CMD_ARG(test, NULL, "test <class|index>: emit a Matter event", CmdTest, 2, 0),
	SHELL_SUBCMD_SET_END);
SHELL_CMD_REGISTER(snd, &snd_cmds, "Sound detector", NULL);

} // namespace

CHIP_ERROR AppTask::Init()
{
	Nrf::Matter::InitData initData;

	initData.mDeviceInfoProvider = &sLabelProvider;
	ReturnErrorOnFailure(Nrf::Matter::PrepareServer(initData));

	if (!Nrf::GetBoard().Init()) {
		LOG_ERR("User interface initialization failed.");
		return CHIP_ERROR_INCORRECT_STATE;
	}
	ReturnErrorOnFailure(Nrf::Matter::RegisterEventHandler(Nrf::Board::DefaultMatterEventHandler, 0));
	ReturnErrorOnFailure(InitIdentify());
	ReturnErrorOnFailure(Nrf::Matter::StartServer());
	device_info_init();
	LOG_INF("%s %s, hardware %s", CONFIG_CHIP_DEVICE_PRODUCT_NAME, CHIP_DEVICE_CONFIG_DEVICE_SOFTWARE_VERSION_STRING,
		device_info_hw_string());

	float diff = dsp_selftest();

	LOG_INF("DSP selftest max diff %.6f %s", (double)diff, diff < DSP_SELFTEST_TOL ? "PASS" : "FAIL");
	detector_init(OnSoundEvent);
	audio_start(OnAudioResult);
	return CHIP_NO_ERROR;
}

CHIP_ERROR AppTask::StartApp()
{
	ReturnErrorOnFailure(Init());

	while (true) {
		Nrf::DispatchNextTask();
	}

	return CHIP_NO_ERROR;
}
