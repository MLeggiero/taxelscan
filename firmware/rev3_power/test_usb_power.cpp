#include "usb_power_policy.h"
#include <cassert>
#include <cstdio>
#include <initializer_list>
using namespace taxelscan;
int main() {
    // All CC advertisements x USB grants x suspend states, from reset.
    for (int cc=0; cc<4; ++cc) for (unsigned ma : {0u,100u,250u,500u})
        for (bool suspended : {false,true}) {
            UsbPowerPolicy p; PowerInputs in;
            in.out1_high=(cc&2)!=0; in.out2_high=(cc&1)!=0;
            in.configured_ma=static_cast<uint16_t>(ma); in.suspended=suspended;
            assert(p.update(0,in)==PowerMode::Off);
            const PowerMode expect=suspended||cc==3?PowerMode::Off:
                cc<2?PowerMode::High:ma==500?PowerMode::Low:PowerMode::Off;
            assert(p.update(5,in)==expect);
        }
    UsbPowerPolicy p; PowerInputs in;
    in.out1_high=false; // 1.5 A advertised; no USB grant necessary.
    p.update(0,in); assert(p.update(5,in)==PowerMode::High);
    in.out1_high=true; in.out2_high=false; // Source reduces to default.
    assert(p.update(6,in)==PowerMode::Off);
    in.configured_ma=500;p.update(7,in);
    assert(p.update(12,in)==PowerMode::Low);
    in.suspended=true;assert(p.update(13,in)==PowerMode::Off);
    in.suspended=false;p.update(14,in);assert(p.update(19,in)==PowerMode::Low);
    in.configured_ma=0;assert(p.update(20,in)==PowerMode::Off); // USB reset.
    in.configured_ma=500;p.update(21,in);p.update(26,in);
    in.fault_n=false;
    assert(p.update(60,in)==PowerMode::Low); // Capacitive startup grace period.
    assert(p.update(76,in)==PowerMode::Off && p.fault_latched());
    in.fault_n=true;assert(p.update(1000,in)==PowerMode::Off); // No fault cycling.
    p.retry();p.update(1001,in);assert(p.update(1006,in)==PowerMode::Low);
    in.out1_high=false;p.update(1007,in);assert(p.update(1012,in)==PowerMode::High);
    in.out1_high=true;assert(p.update(1013,in)==PowerMode::Low); // Immediate downgrade.
    in.requested=false;assert(p.update(1014,in)==PowerMode::Off);
    // Millisecond counter rollover must not keep a stale power grant enabled.
    p.retry();in.requested=true;p.update(0xfffffffeu,in);
    assert(p.update(3,in)==PowerMode::Low);
    in.out2_high=true;assert(p.update(4,in)==PowerMode::Off);
    std::puts("USB power policy: 32 startup combinations and transition/fault/rollover cases passed");
}
