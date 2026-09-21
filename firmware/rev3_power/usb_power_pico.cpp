#include "usb_power_pico.h"
#include "usb_power_policy.h"
#include <initializer_list>
#include "hardware/gpio.h"
#include "hardware/sync.h"
#include "pico/time.h"
#include "tusb.h"

namespace {
taxelscan::UsbPowerPolicy policy;
repeating_timer_t timer;
bool requested = true;
bool initialized = false;

void apply(taxelscan::PowerMode mode) {
    using namespace taxelscan;
    // Disable before changing to OFF; choose current limit before enabling.
    if (mode == PowerMode::Off) gpio_put(USB_BUS_EN, false);
    gpio_put(USB_ILIM_HI, mode == PowerMode::High);
    if (mode != PowerMode::Off) gpio_put(USB_BUS_EN, true);
}

bool tick(repeating_timer_t*) {
    using namespace taxelscan;
    const uint32_t pins = gpio_get_all(); // Read both CC bits together.
    PowerInputs in;
    in.out1_high = (pins & (1u << USB_CC_OUT1)) != 0;
    in.out2_high = (pins & (1u << USB_CC_OUT2)) != 0;
    in.fault_n = (pins & (1u << USB_PWR_FAULT)) != 0;
    in.requested = requested;
    in.suspended = tud_suspended();
    // Use the actual descriptor; a stock 100/250 mA descriptor must not be
    // mistaken for a 500 mA grant. Only a single configuration is supported.
    if (tud_mounted()) {
        const uint8_t* device = tud_descriptor_device_cb();
        const uint8_t* cfg = tud_descriptor_configuration_cb(0);
        if (device && cfg && device[0] >= 18 && device[17] == 1 &&
            cfg[0] >= 9 && cfg[1] == TUSB_DESC_CONFIGURATION && cfg[5] == 1)
            in.configured_ma = uint16_t(cfg[8]) * 2;
    }
    apply(policy.update(to_ms_since_boot(get_absolute_time()), in));
    return true;
}
}

bool taxelscan_usb_power_init() {
    using namespace taxelscan;
    if (initialized) return true;
    for (unsigned pin : {USB_BUS_EN, USB_ILIM_HI}) {
        gpio_init(pin); gpio_put(pin, false); gpio_set_dir(pin, GPIO_OUT);
    }
    for (unsigned pin : {USB_CC_OUT1, USB_CC_OUT2, USB_PWR_FAULT}) {
        gpio_init(pin); gpio_set_dir(pin, GPIO_IN); gpio_disable_pulls(pin);
    }
    initialized = add_repeating_timer_ms(-1, tick, nullptr, &timer);
    return initialized;
}
void taxelscan_usb_power_enable(bool on) {
    const uint32_t irq = save_and_disable_interrupts();
    requested = on;
    if (!on) { apply(taxelscan::PowerMode::Off); policy.disable(); }
    restore_interrupts(irq);
}
void taxelscan_usb_power_retry() {
    const uint32_t irq = save_and_disable_interrupts();
    apply(taxelscan::PowerMode::Off);
    policy.retry();
    restore_interrupts(irq);
}
bool taxelscan_usb_power_fault() {
    const uint32_t irq = save_and_disable_interrupts();
    const bool fault = policy.fault_latched();
    restore_interrupts(irq);
    return fault;
}
