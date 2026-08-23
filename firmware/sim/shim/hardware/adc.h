// ADC shim: declarations only. scan.h defines readAdc() as static inline, so
// these must compile, but the sim never calls it.
#pragma once
#include <stdint.h>
void     adc_init(void);
void     adc_gpio_init(uint8_t);
void     adc_select_input(uint8_t);
uint16_t adc_read(void);
void     adc_set_temp_sensor_enabled(bool);
