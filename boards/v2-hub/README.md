# Hub — not started

The board the eight front-end modules plug into. Nothing here yet; the design
lives in the v2 plan and is summarised below so this directory is not a mystery.

| Block | Part | Note |
|---|---|---|
| MCU | STM32H743ZIT6, LQFP144 | 480 MHz M7, 1 MB SRAM, 3× 16-bit ADC |
| USB | USB3320C ULPI PHY + 24 MHz crystal | The H7 has no embedded HS PHY, and 8 maps × 100 Hz = 1.6 MB/s needs High-Speed |
| Fanout | 4× 74LVC16244A | 7 signals × 8 modules = 56 buffered outputs, series-terminated per branch |
| Protection | 8× TPS2553 | Current-limited per module: hot-plug inrush through 0.41 Ω of cable is otherwise unlimited |
| Housekeeping | 1× CD74HC4067 → ADC3 | 8× ROW_VCC_SENSE, sampled once per row |
| Power | buck 5 V→3.6 V, then LDOs for VDD_D / ROW_VCC / AVCC | ~572 mA total. USB's 500 mA default is not enough, so a separate 5 V input is required |
| Connectors | 8× FH12-20S-0.5SH | Pinout in `../v2-module/README.md` |

Unlike the module, this has no proven predecessor to derive from, so it cannot
be generated the same way. The hard part is pin assignment: the ULPI interface
consumes seven ADC-capable pins, ADC3 sits in domain D3 where its DMA reaches
only SRAM4, and every DMA buffer needs an MPU non-cacheable region. Those
constraints have to be worked in CubeMX against the exact package before
layout.
