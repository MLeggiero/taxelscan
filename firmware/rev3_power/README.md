# Rev3 USB chain-power controller

`usb_power_policy.h` contains the portable decision logic. `usb_power_pico.cpp`
provides the RP2354A/Pico SDK adapter with a 1 ms hardware timer and TinyUSB
configuration checking. This directory does not replace the rev1 scanning
application or implement rev3 ADC/RS485 acquisition.

Add `usb_power_pico.cpp` to the rev3 Pico SDK executable and link `pico_stdlib`,
`hardware_gpio`, `hardware_sync`, and `tinyusb_device`. Initialize it on core 0
after initializing TinyUSB:

```cpp
#include "usb_power_pico.h"
// After tusb_init() / board initialization:
if (!taxelscan_usb_power_init()) {
    // Report a power-controller timer failure; the feed remains disabled.
}
```

Use one USB configuration (configuration number 1), with a **500 mA** power
request in its descriptor, for example the `500` argument to TinyUSB's
`TUD_CONFIG_DESCRIPTOR`. The adapter reads the descriptor's `bMaxPower` itself.
It does not assume that opening a serial terminal grants current. Smaller
descriptor requests leave the default-current branch disabled.

Service `tud_task()` promptly, at least every millisecond. It must process
configuration, reset, and suspend events without waiting for a scan frame or
serial write. The timer handles CC changes independently of normal scan work;
do not mask interrupts for long periods. Use the hardware watchdog to reset
GPIOs if the application stops servicing its tasks. Call the control APIs from
core 0; the adapter's critical sections are not a cross-core synchronization API.

`taxelscan_usb_power_enable(false)` disables the branch. `...enable(true)`
requests it within the currently available source budget. `...fault()` reports
a latched fault, and `...retry()` authorizes one new startup attempt. Neither
call overrides the source's current advertisement.

Before configuration, keep the master's USB input current at or below 100 mA.
Implement and measure the master's own USB suspend behavior. The downstream
switch alone does not establish the complete board's USB power compliance.
See [USB_POWER.md](../../boards/rev3/USB_POWER.md) for the circuit and budgets.

## Native policy verification

The policy tests need only a C++17 compiler:

```sh
c++ -std=c++17 -Wall -Wextra -Werror test_usb_power.cpp -o test_usb_power
./test_usb_power
```

They cover all 32 CC/USB-grant/suspend combinations, source downgrades,
configuration loss, startup grace, fault latching, explicit retry, request
disable, and timer rollover. These are native logic tests; a Pico SDK target
build and physical USB power measurements are separate validation steps.
