/*
 * Basic Information overrides on top of the factory data provider:
 * - VendorName from Kconfig (factory data only changes with a USB flash, OTA keeps it)
 * - HardwareVersionString: board name + SoC part and variant (silicon revision) from FICR
 * - SerialNumber: the chip's unique FICR DEVICEID instead of the shared default
 * Everything else (VID/PID, attestation, onboarding) comes from the factory data.
 */

#include "device_info.h"

#include <cstdio>
#include <cstring>

#include <hal/nrf_ficr.h>
#include <platform/DeviceInstanceInfoProvider.h>
#include <platform/PlatformManager.h>

using namespace chip;
using namespace chip::DeviceLayer;

namespace {

#define BOARD_NAME "Seeed XIAO nRF54LM20A Sense"

CHIP_ERROR CopyString(const char *src, char *buf, size_t bufSize)
{
	size_t len = strlen(src);

	VerifyOrReturnError(len < bufSize, CHIP_ERROR_BUFFER_TOO_SMALL);
	memcpy(buf, src, len + 1);
	return CHIP_NO_ERROR;
}

/* "nRF54LM20A AAB0": part from FICR INFO.PART, variant (silicon revision) from INFO.VARIANT. */
void SocString(char *buf, size_t bufSize)
{
	uint32_t part = NRF_FICR->INFO.PART;
	uint32_t variant = NRF_FICR->INFO.VARIANT;
	char var[5];

	for (int i = 0; i < 4; i++) {
		char ch = (char)(variant >> (24 - 8 * i));

		var[i] = (ch >= 0x20 && ch < 0x7f) ? ch : '?';
	}
	var[4] = '\0';
	switch (part) {
	case FICR_INFO_PART_PART_N54LM20A:
		snprintf(buf, bufSize, "nRF54LM20A %s", var);
		break;
	case FICR_INFO_PART_PART_N54LM20B:
		snprintf(buf, bufSize, "nRF54LM20B %s", var);
		break;
	default:
		snprintf(buf, bufSize, "part %08X %s", (unsigned)part, var);
		break;
	}
}

class InfoProvider : public DeviceInstanceInfoProvider {
public:
	void SetBase(DeviceInstanceInfoProvider *base) { mBase = base; }

	CHIP_ERROR GetVendorName(char *buf, size_t bufSize) override
	{
		return CopyString(CONFIG_CHIP_DEVICE_VENDOR_NAME, buf, bufSize);
	}
	CHIP_ERROR GetVendorId(uint16_t &v) override { return mBase->GetVendorId(v); }
	CHIP_ERROR GetProductName(char *buf, size_t n) override { return mBase->GetProductName(buf, n); }
	CHIP_ERROR GetProductId(uint16_t &v) override { return mBase->GetProductId(v); }
	CHIP_ERROR GetPartNumber(char *buf, size_t n) override { return mBase->GetPartNumber(buf, n); }
	CHIP_ERROR GetProductURL(char *buf, size_t n) override { return mBase->GetProductURL(buf, n); }
	CHIP_ERROR GetProductLabel(char *buf, size_t n) override { return mBase->GetProductLabel(buf, n); }
	CHIP_ERROR GetManufacturingDate(uint16_t &y, uint8_t &m, uint8_t &d) override
	{
		return mBase->GetManufacturingDate(y, m, d);
	}
	CHIP_ERROR GetHardwareVersion(uint16_t &v) override { return mBase->GetHardwareVersion(v); }
	CHIP_ERROR GetRotatingDeviceIdUniqueId(MutableByteSpan &span) override
	{
		return mBase->GetRotatingDeviceIdUniqueId(span);
	}

	CHIP_ERROR GetHardwareVersionString(char *buf, size_t bufSize) override
	{
		return CopyString(device_info_hw_string(), buf, bufSize);
	}

	CHIP_ERROR GetSerialNumber(char *buf, size_t bufSize) override
	{
		VerifyOrReturnError(bufSize > 16, CHIP_ERROR_BUFFER_TOO_SMALL);
		snprintf(buf, bufSize, "%08X%08X", (unsigned)NRF_FICR->INFO.DEVICEID[1],
			 (unsigned)NRF_FICR->INFO.DEVICEID[0]);
		return CHIP_NO_ERROR;
	}

private:
	DeviceInstanceInfoProvider *mBase = nullptr;
};

InfoProvider sProvider;

} // namespace

const char *device_info_hw_string()
{
	static char str[65];

	if (!str[0]) {
		char soc[32];

		SocString(soc, sizeof(soc));
		snprintf(str, sizeof(str), "%s, %s", BOARD_NAME, soc);
	}
	return str;
}

void device_info_init()
{
	DeviceInstanceInfoProvider *base = GetDeviceInstanceInfoProvider();

	if (base == nullptr || base == &sProvider) {
		return;
	}
	sProvider.SetBase(base);
	PlatformMgr().LockChipStack();
	SetDeviceInstanceInfoProvider(&sProvider);
	PlatformMgr().UnlockChipStack();
}
