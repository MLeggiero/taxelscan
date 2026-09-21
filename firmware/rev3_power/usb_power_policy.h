#pragma once
#include <stdint.h>

namespace taxelscan {
constexpr unsigned USB_BUS_EN = 24;   // rev-3 layout 2026-09-10: moved off the QFN bottom row (was 12)
constexpr unsigned USB_ILIM_HI = 0;   // was 13
constexpr unsigned USB_CC_OUT1 = 27;  // was 14
constexpr unsigned USB_CC_OUT2 = 29;  // was 15
constexpr unsigned USB_PWR_FAULT = 23;
constexpr unsigned USB_CONFIGURATION_MA = 500;

enum class PowerMode : uint8_t { Off, Low, High };
struct PowerInputs {
    bool out1_high = true;
    bool out2_high = true;
    bool suspended = false;
    bool fault_n = true;
    bool requested = true;
    uint16_t configured_ma = 0;  // Zero until SET_CONFIGURATION succeeds.
};

// No dependency on scan timing, serial DTR, sensor count, or an estimated load.
// Call at least every millisecond. The hardware limits current during startup.
class UsbPowerPolicy {
public:
    PowerMode update(uint32_t now_ms, const PowerInputs& in) {
        const bool attached = !(in.out1_high && in.out2_high);
        if (!attached) fault_latched_ = false;  // Physical unplug permits retry.
        if (mode_ != PowerMode::Off && !in.fault_n &&
            uint32_t(now_ms - enabled_at_) >= 50) fault_latched_ = true;

        PowerMode wanted = PowerMode::Off;
        if (attached && in.requested && !in.suspended && !fault_latched_) {
            if (!in.out1_high) wanted = PowerMode::High; // CC = 1.5 A or 3 A.
            else if (in.configured_ma >= USB_CONFIGURATION_MA) wanted = PowerMode::Low;
        }
        // Revoke power/budget immediately; qualify every increase for 5 ms.
        if (wanted == PowerMode::Off) {
            mode_ = pending_ = PowerMode::Off;
        } else if (mode_ == PowerMode::High && wanted == PowerMode::Low) {
            mode_ = pending_ = PowerMode::Low;
        } else if (wanted != mode_) {
            if (pending_ != wanted) { pending_ = wanted; pending_at_ = now_ms; }
            else if (uint32_t(now_ms - pending_at_) >= 5) {
                if (mode_ == PowerMode::Off) enabled_at_ = now_ms;
                mode_ = wanted;
            }
        } else pending_ = wanted;
        return mode_;
    }
    // Caller must disable the output first. A retry grants one startup window.
    void retry() { fault_latched_ = false; mode_ = pending_ = PowerMode::Off; }
    void disable() { mode_ = pending_ = PowerMode::Off; }
    bool fault_latched() const { return fault_latched_; }
    PowerMode mode() const { return mode_; }
private:
    PowerMode mode_ = PowerMode::Off, pending_ = PowerMode::Off;
    uint32_t pending_at_ = 0, enabled_at_ = 0;
    bool fault_latched_ = false;
};
} // namespace taxelscan
