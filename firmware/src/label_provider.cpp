#include "label_provider.h"

#include <cctype>
#include <cstring>

#include <lib/support/CHIPMemString.h>
#include <platform/CHIPDeviceLayer.h>

#include "model.h"

using namespace chip;

namespace {

constexpr char kLabelKey[] = "ha_entitylabel";
char sPretty[MD_N_CLASSES][DeviceLayer::kMaxLabelValueLength + 1];

class SingleLabelIterator : public DeviceLayer::DeviceInfoProvider::FixedLabelIterator {
public:
	explicit SingleLabelIterator(const char *value) : mValue(value) {}
	size_t Count() override { return 1; }
	bool Next(DeviceLayer::DeviceInfoProvider::FixedLabelType &output) override
	{
		if (mDone) {
			return false;
		}
		output.label = CharSpan::fromCharString(kLabelKey);
		output.value = CharSpan::fromCharString(mValue);
		mDone = true;
		return true;
	}
	void Release() override { Platform::Delete(this); }

private:
	const char *mValue;
	bool mDone = false;
};

} // namespace

const char *SoundLabelProvider::PrettyName(int cls)
{
	if (cls < 0 || cls >= MD_N_CLASSES) {
		return "";
	}
	char *out = sPretty[cls];

	if (out[0] == '\0') {
		/* "dog_bark" -> "Dog bark" */
		Platform::CopyString(out, sizeof(sPretty[cls]), md_class_names[cls]);
		for (char *p = out; *p; p++) {
			if (*p == '_') {
				*p = ' ';
			}
		}
		out[0] = static_cast<char>(toupper(out[0]));
	}
	return out;
}

DeviceLayer::DeviceInfoProvider::FixedLabelIterator *SoundLabelProvider::IterateFixedLabel(EndpointId endpoint)
{
	if (endpoint < kFirstEndpoint || endpoint >= kFirstEndpoint + MD_N_CLASSES) {
		return nullptr;
	}
	return Platform::New<SingleLabelIterator>(PrettyName(endpoint - kFirstEndpoint));
}
