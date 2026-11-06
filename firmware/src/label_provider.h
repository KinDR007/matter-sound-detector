#pragma once

#include <DeviceInfoProviderImpl.h>

/*
 * Fixed Label per sound endpoint: {"ha_entitylabel": "<Class name>"}.
 * Home Assistant uses this label to name Matter entities of test/DIY devices
 * (vendor 0xFFF1, product 0x8000..0x8002).
 */
class SoundLabelProvider : public chip::DeviceLayer::DeviceInfoProviderImpl {
public:
	FixedLabelIterator *IterateFixedLabel(chip::EndpointId endpoint) override;

	/* Endpoint of the first sound class; class i lives on kFirstEndpoint + i. */
	static constexpr chip::EndpointId kFirstEndpoint = 1;

	static const char *PrettyName(int cls);
};
