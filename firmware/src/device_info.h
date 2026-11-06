#pragma once

/* Basic Information overrides (vendor name, hardware string, serial number), see device_info.cpp. */

/* Install the provider; call after Nrf::Matter::StartServer(). */
void device_info_init();

/* "Seeed XIAO nRF54LM20A Sense, nRF54LM20A AAB0" (board, SoC part and variant). */
const char *device_info_hw_string();
