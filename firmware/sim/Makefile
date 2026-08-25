# Portable build of the native conditioning simulation (Linux/macOS, gcc/clang).
#
# build.cmd does the same job on Windows with MSVC. Both compile the SHIPPED
# condition.cpp unmodified against the shims in sim/, so whichever one you run,
# you are exercising the code that goes on the board.
#
#   make            build ./sim
#   make test       build and run the A-G checks
#   make bench      build and run the timing harness
#   make clean

CXX      ?= g++
CXXFLAGS ?= -O2 -std=c++17 -Wall -Wno-unused-result
INCLUDES  = -Ishim -I../taxelscan
SRC       = simtest.cpp ../taxelscan/condition.cpp
BIN       = sim
BIN_FIXED = sim-fixed

.PHONY: all test bench compare clean
all: $(BIN)

# Ships as built: float one-euro. See the note in condition.h for why the
# fixed-point experiment did not win.
$(BIN): $(SRC)
	$(CXX) $(CXXFLAGS) $(INCLUDES) -DTAXEL_EURO_FIXED=0 -o $@ $(SRC) -lm

# The fixed-point alternative, kept proven-equivalent so the decision above
# stays evidence-backed and so it is ready for an FPU-less target.
$(BIN_FIXED): $(SRC)
	$(CXX) $(CXXFLAGS) $(INCLUDES) -DTAXEL_EURO_FIXED=1 -o $@ $(SRC) -lm

test: $(BIN)
	./$(BIN) $(MINUTES)

bench: $(BIN)
	./$(BIN) bench

# Fixed point must reach the same VERDICTS as float on every A-G check and the
# same gating decisions every frame. The diagnostic numbers printed alongside
# each check are expected to shift by a count or two - that is the Q8 rounding
# floor, and comparing them would only ever produce a false alarm.
MINUTES ?= 5
compare: $(BIN) $(BIN_FIXED) | obj
	@echo "=== one-euro arithmetic (fixed build only) ==="
	@./$(BIN_FIXED) euro > obj/euro-fixed.csv
	@./$(BIN)       euro > obj/euro-float.csv 2>/dev/null
	@echo ""
	@echo "=== pipeline divergence, fixed vs float ==="
	@python3 compare_euro.py obj/euro-fixed.csv obj/euro-float.csv
	@echo ""
	@echo "=== A-G verdicts ==="
	@./$(BIN)       $(MINUTES) | sed -n 's/^  \(ok\|FAIL\)  *\([^-]*\).*/\1 \2/p' > obj/v-float.txt
	@./$(BIN_FIXED) $(MINUTES) | sed -n 's/^  \(ok\|FAIL\)  *\([^-]*\).*/\1 \2/p' > obj/v-fixed.txt
	@if diff -q obj/v-float.txt obj/v-fixed.txt >/dev/null; then \
	   echo "  identical on all $$(wc -l < obj/v-float.txt) checks," \
	        "$$(grep -c '^ok' obj/v-float.txt) passing"; \
	 else diff obj/v-float.txt obj/v-fixed.txt; exit 1; fi

obj:
	@mkdir -p obj

clean:
	rm -rf obj $(BIN) $(BIN_FIXED)
