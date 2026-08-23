// SIO shim: a dummy register block so scan.h's selectChan() compiles.
#pragma once
#include <stdint.h>
typedef struct { volatile uint32_t gpio_out, gpio_set, gpio_clr; } sio_hw_t;
extern sio_hw_t *sio_hw;
