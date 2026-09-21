#pragma once

// Pico SDK + TinyUSB adapter for the rev3 board, not the rev1 XIAO firmware.
// Initialize once on core 0 after tusb_init(). False leaves the feed disabled.
bool taxelscan_usb_power_init();
void taxelscan_usb_power_enable(bool requested);
void taxelscan_usb_power_retry();
bool taxelscan_usb_power_fault();
