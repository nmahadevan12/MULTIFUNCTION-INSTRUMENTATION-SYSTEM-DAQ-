"""
Testbench UI - Navigation Skeleton (Checkpoint C)
Group 15

Implements the full menu tree from the project spec:
  - Function Generator (Sine / Square)
  - Ohmmeter
  - Voltmeter (External / Internal Ref)
  - DC Reference
  - Frequency Measurement

Tolerances shown on-screen:
  Ohmmeter          +/- 10% of reading
  Voltmeter         +/- 0.2 V
  DC Reference      +/- 0.2 V
  Freq Measure      +/- 1% of reading
  Square amplitude  +/- 1 V
  Sine amplitude    +/- 0.2 V
"""

from gpiozero import RotaryEncoder, Button
from RPLCD.i2c import CharLCD
from time import sleep, time
import atexit
import os
import signal
import smbus2
import subprocess
import sys
import threading
import statistics
import RPi.GPIO as GPIO
import spidev

try:
    import pigpio
except ImportError:
    pigpio = None

# ==========================================
# 1. HARDWARE PINS & SETUP
# ==========================================
rotor = RotaryEncoder(13, 19, wrap=False, max_steps=0)
btn   = Button(26, hold_time=3)

# ---- Function Generator Hardware ----
FG_PWM_PIN  = 12
FG_SPI_BUS  = 0
FG_SPI_DEV  = 1
FG_CE0_GPIO = 8

AMP_SCALE    = 0.63
OFFSET_RATIO = 0.45

# ---- Tolerance constants (displayed to user) ----
OHM_TOL_FRACTION  = 0.10    # +/- 10% of reading
VOLT_TOL_V        = 0.2     # +/- 0.2 V on voltmeter
DC_TOL_V          = 0.2     # +/- 0.2 V on DC reference
FREQ_TOL_FRACTION = 0.01    # +/- 1% on frequency meter
SQUARE_AMP_TOL_V  = 1.0     # +/- 1 V on square-wave amplitude
SINE_AMP_TOL_V    = 0.2     # +/- 0.2 V on sine-wave amplitude

pi_fg = None
if pigpio is not None:
    try:
        _fg_candidate = pigpio.pi()
        if _fg_candidate.connected:
            pi_fg = _fg_candidate
        else:
            _fg_candidate.stop()
    except Exception:
        pi_fg = None

spi_fg = None
if pi_fg is not None:
    spi_fg = spidev.SpiDev()
    spi_fg.open(FG_SPI_BUS, FG_SPI_DEV)
    spi_fg.max_speed_hz = 1_000_000


# ---- Sine Wave Hardware (MCP4131 + audio jack) ----
SINE_CS_PIN    = 17
SINE_FREQ_MIN  = 1000
SINE_FREQ_MAX  = 10000
SINE_FREQ_STEP = 500

SINE_STEP_MIN = 1
SINE_STEP_MAX = 124

SINE_ALSA_DEVICE = "hw:2,0"
SINE_ALSA_VOLUME = 82

SINE_VOLTAGE_DATA = {
    0: 10.1, 1: 9.8, 2: 9.6, 3: 9.5, 4: 9.4, 5: 9.2,
    6: 9.1, 7: 9.0, 8: 8.9, 9: 8.8, 10: 8.6, 11: 8.5,
    12: 8.4, 13: 8.3, 14: 8.2, 15: 8.1, 16: 8.0, 17: 7.6,
    18: 7.55, 19: 7.45, 20: 7.3, 21: 7.25, 22: 7.15, 23: 7.05,
    24: 6.95, 25: 6.9, 26: 6.8, 27: 6.75, 28: 6.65, 29: 6.6,
    30: 6.45, 31: 6.4, 32: 6.35, 33: 6.25, 34: 6.15, 35: 6.0,
    36: 5.95, 37: 5.9, 38: 5.85, 39: 5.75, 40: 5.65, 41: 5.55,
    42: 5.5, 43: 5.5, 44: 5.45, 45: 5.35, 46: 5.2, 47: 5.15,
    48: 5.1, 49: 5.05, 50: 4.95, 51: 4.95, 52: 4.85, 53: 4.75,
    54: 4.7, 55: 4.7, 56: 4.6, 57: 4.55, 58: 4.5, 59: 4.4,
    60: 4.3, 61: 4.3, 62: 4.25, 63: 4.2, 64: 4.1, 65: 3.92,
    66: 3.88, 67: 3.8, 68: 3.76, 69: 3.72, 70: 3.64, 71: 3.6,
    72: 3.56, 73: 3.48, 74: 3.4, 75: 3.36, 76: 3.32, 77: 3.26,
    78: 3.2, 79: 3.16, 80: 3.08, 81: 3.04, 82: 3.0, 83: 2.91,
    84: 2.85, 85: 2.79, 86: 2.75, 87: 2.71, 88: 2.63, 89: 2.59,
    90: 2.51, 91: 2.47, 92: 2.43, 93: 2.39, 94: 2.31, 95: 2.25,
    96: 2.19, 97: 2.15, 98: 2.07, 99: 2.03, 100: 1.97, 101: 1.89,
    102: 1.85, 103: 1.79, 104: 1.73, 105: 1.67, 106: 1.62, 107: 1.56,
    108: 1.5, 109: 1.43, 110: 1.37, 111: 1.32, 112: 1.26, 113: 1.19,
    114: 1.14, 115: 1.07, 116: 1.01, 117: 0.94, 118: 0.86, 119: 0.8,
    120: 0.53, 121: 0.63, 122: 0.59, 123: 0.55, 124: 0.46,
}
_SINE_SORTED_STEPS = sorted(SINE_VOLTAGE_DATA.keys())

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)
GPIO.setup(SINE_CS_PIN, GPIO.OUT)
GPIO.output(SINE_CS_PIN, GPIO.HIGH)


def initialize_lcd(address=0x27, port=1, retries=3):
    try:
        bus = smbus2.SMBus(port)
        bus.read_byte(address)
        bus.close()
    except OSError:
        pass
    sleep(0.1)
    for attempt in range(retries):
        try:
            _lcd = CharLCD('PCF8574', address, port=port, cols=20, rows=4)
            _lcd.auto_linebreaks = False
            return _lcd
        except OSError as e:
            if attempt < retries - 1:
                sleep(0.5)
            else:
                raise e

lcd = initialize_lcd()
sleep(0.3)


# ---- Ohmmeter Hardware ----
OHM_CS_PIN    = 8
OHM_COMP_PIN  = 21
OHM_MAX_STEPS = 128
OHM_KNOWN_R   = 10000.0

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)
GPIO.setup(OHM_CS_PIN, GPIO.OUT)
GPIO.output(OHM_CS_PIN, GPIO.HIGH)
GPIO.setup(OHM_COMP_PIN, GPIO.IN)

spi_meas = spidev.SpiDev()
spi_meas.open(0, 0)
spi_meas.max_speed_hz = 1_000_000


# ---- Voltmeter Hardware ----
VOLT_COMP_PIN  = 6
VOLT_MAX_STEPS = 128

GPIO.setup(VOLT_COMP_PIN, GPIO.IN)

VOLTAGE_DATA = {
    0: -4.95, 1: -4.80, 3: -4.70, 4: -4.60, 5: -4.50, 7: -4.40, 8: -4.30, 9: -4.20,
    10: -4.10, 12: -4.00, 13: -3.90, 14: -3.80, 15: -3.70, 17: -3.60, 18: -3.50,
    19: -3.40, 20: -3.30, 22: -3.20, 23: -3.10, 25: -3.00, 26: -2.90, 27: -2.80,
    28: -2.70, 29: -2.60, 31: -2.50, 32: -2.40, 33: -2.30, 34: -2.20, 36: -2.10,
    37: -2.00, 39: -1.90, 40: -1.80, 41: -1.70, 42: -1.60, 43: -1.50, 45: -1.40,
    46: -1.30, 47: -1.20, 49: -1.10, 50: -1.00, 51: -0.90, 52: -0.80, 54: -0.70,
    55: -0.60, 56: -0.50, 57: -0.40, 58: -0.30, 60: -0.20, 61: -0.10, 62: 0.00,
    64: 0.10, 65: 0.20, 66: 0.30, 67: 0.40, 68: 0.50, 70: 0.60, 71: 0.70, 72: 0.80,
    74: 0.90, 75: 1.00, 76: 1.10, 77: 1.20, 79: 1.30, 80: 1.40, 81: 1.50, 82: 1.60,
    84: 1.70, 85: 1.80, 86: 1.90, 87: 2.00, 89: 2.10, 90: 2.20, 92: 2.30, 93: 2.40,
    94: 2.50, 95: 2.60, 96: 2.70, 98: 2.80, 99: 2.90, 100: 3.00, 101: 3.10, 103: 3.20,
    104: 3.30, 105: 3.40, 106: 3.50, 108: 3.60, 109: 3.70, 110: 3.80, 112: 3.90,
    113: 4.00, 114: 4.10, 116: 4.20, 117: 4.30, 118: 4.40, 119: 4.50, 120: 4.60,
    122: 4.70, 123: 4.80, 124: 4.90, 125: 5.00
}
_VOLT_SORTED_STEPS = sorted(VOLTAGE_DATA.keys())


# ---- DC Reference Hardware ----
DAC_PINS   = [14, 15, 18, 23, 24]
DAC_LEVELS = 32
DAC_V_MIN  = -5.00
DAC_V_MAX  =  4.80
DAC_STEP   = (DAC_V_MAX - DAC_V_MIN) / (DAC_LEVELS - 1)

for _pin in DAC_PINS:
    GPIO.setup(_pin, GPIO.OUT)
    GPIO.output(_pin, GPIO.LOW)


# ---- Frequency Meter Hardware ----
FREQ_PIN        = 25
FREQ_SAMPLE_CAP = 100

pi_freq = None
if pigpio is not None:
    try:
        _candidate = pigpio.pi()
        if _candidate.connected:
            pi_freq = _candidate
            pi_freq.set_mode(FREQ_PIN, pigpio.INPUT)
            pi_freq.set_pull_up_down(FREQ_PIN, pigpio.PUD_OFF)
        else:
            _candidate.stop()
    except Exception:
        pi_freq = None


# ==========================================
# 2. STATE VARIABLES
# ==========================================
current_freq      = 1000
current_amp_step  = 90
sine_amp_step     = 64
fg_wave_type      = "Square"
fg_output_on      = False

sine_speaker_process = None

dc_voltage   = 0.0
dc_index     = 16
dc_output_on = False

volt_source = "External"

current_resistance = None
current_voltage    = None

edge_times        = []
last_tick         = None
freq_cb           = None
current_frequency = None
current_period_ms = None
freq_sample_count = 0

MEAS_INTERVAL  = 0.5
last_meas_time = 0.0

in_edit_mode = False
edit_target  = None

FREQ_FAST = 100
FREQ_SLOW = 10
AMP_FAST  = 5
AMP_SLOW  = 1

FAST_THRESHOLD   = 0.05
last_click_time  = 0
last_rotor_value = 0

last_release_time = 0
ignore_next_click = False

display_needs_update = True

should_exit = False


# ==========================================
# 3. FULL MENU TREE
# ==========================================
menu_tree = {
    "MAIN":        ["OFF", "Mode Select", "Exit"],
    "MODE_SELECT": ["Func Gen", "Ohmmeter", "Voltmeter", "DC Ref",
                    "Freq Meas", "Back", "Main"],

    "FG_MENU":  ["Type", "Frequency", "Amplitude", "Output", "Back", "Main"],
    "FG_TYPE":  ["Sine", "Square", "Back", "Main"],
    "FG_FREQ":  ["Input Freq", "Back", "Main"],
    "FG_AMP":   ["Input Amp", "Back", "Main"],
    "FG_OUT":   ["On", "Off", "Back", "Main"],

    "OHMMETER": ["Back", "Main"],

    "VOLTMETER": ["Source", "Back", "Main"],
    "VOLT_SRC":  ["External", "Internal Ref", "Back", "Main"],

    "DC_REF":  ["Voltage Input", "Output", "Back", "Main"],
    "DC_OUT":  ["On", "Off", "Back", "Main"],

    "FREQ_MEAS": ["Back", "Main"],
}

current_menu = "MAIN"
menu_index   = 0
menu_history = []


# ==========================================
# 4. HARDWARE FUNCTIONS
# ==========================================
def set_sine_wiper(step):
    if spi_meas is None:
        return
    step = max(0, min(128, int(step)))
    GPIO.output(SINE_CS_PIN, GPIO.LOW)
    spi_meas.xfer2([0x00, step])
    GPIO.output(SINE_CS_PIN, GPIO.HIGH)

def sine_step_to_voltage(step):
    if step in SINE_VOLTAGE_DATA:
        return SINE_VOLTAGE_DATA[step]
    if step <= _SINE_SORTED_STEPS[0]:
        return SINE_VOLTAGE_DATA[_SINE_SORTED_STEPS[0]]
    if step >= _SINE_SORTED_STEPS[-1]:
        return SINE_VOLTAGE_DATA[_SINE_SORTED_STEPS[-1]]
    low_s  = max(k for k in _SINE_SORTED_STEPS if k < step)
    high_s = min(k for k in _SINE_SORTED_STEPS if k > step)
    v_low, v_high = SINE_VOLTAGE_DATA[low_s], SINE_VOLTAGE_DATA[high_s]
    return v_low + (v_high - v_low) * (step - low_s) / (high_s - low_s)

def _amixer(*args):
    try:
        subprocess.run(["amixer", *args],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=2)
    except Exception:
        pass

def stop_sine_audio():
    global sine_speaker_process
    if sine_speaker_process is not None:
        try:
            os.killpg(os.getpgid(sine_speaker_process.pid), signal.SIGTERM)
            sine_speaker_process.wait(timeout=1)
        except Exception:
            try:
                os.killpg(os.getpgid(sine_speaker_process.pid), signal.SIGKILL)
            except Exception:
                pass
        sine_speaker_process = None
    try:
        subprocess.run(["pkill", "-9", "-f", "speaker-test"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=1)
    except Exception:
        pass
    _amixer("set", "Master", "mute")

def play_sine_audio(frequency):
    global sine_speaker_process
    stop_sine_audio()
    _amixer("set", "Master", f"{SINE_ALSA_VOLUME}%")
    _amixer("set", "Master", "unmute")
    try:
        sine_speaker_process = subprocess.Popen(
            ["speaker-test", "-D", SINE_ALSA_DEVICE, "-t", "sine",
             "-f", str(frequency), "-c", "1", "-X"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
    except FileNotFoundError:
        sine_speaker_process = None

def apply_fg_state():
    if fg_output_on and fg_wave_type == "Square":
        stop_sine_audio()
        set_sine_wiper(128)
        if pi_fg is None or spi_fg is None:
            return
        scaled_amp  = int(current_amp_step * AMP_SCALE)
        offset_step = int(scaled_amp * OFFSET_RATIO)
        amp_wiper   = max(0, min(128, 128 - scaled_amp))
        off_wiper   = max(0, min(128, 128 - offset_step))
        print(f"[FG] square amp={current_amp_step} aw={amp_wiper} "
              f"ow={off_wiper} freq={current_freq}")
        spi_fg.xfer2([0x10, amp_wiper])
        spi_fg.xfer2([0x00, off_wiper])
        pi_fg.hardware_PWM(FG_PWM_PIN, current_freq, 500_000)

    elif fg_output_on and fg_wave_type == "Sine":
        if pi_fg is not None:
            pi_fg.hardware_PWM(FG_PWM_PIN, 0, 0)
        if spi_fg is not None:
            spi_fg.xfer2([0x10, 128]); spi_fg.xfer2([0x00, 128])
        set_sine_wiper(sine_amp_step)
        print(f"[FG] sine amp_step={sine_amp_step} "
              f"({sine_step_to_voltage(sine_amp_step):.2f} V) freq={current_freq}")
        play_sine_audio(current_freq)

    else:
        stop_sine_audio()
        set_sine_wiper(128)
        if pi_fg is not None:
            pi_fg.hardware_PWM(FG_PWM_PIN, 0, 0)
        if spi_fg is not None:
            spi_fg.xfer2([0x10, 128]); spi_fg.xfer2([0x00, 128])

def index_to_dc_voltage(idx):
    return DAC_V_MIN + idx * DAC_STEP

def set_dc_output(idx):
    idx = max(0, min(DAC_LEVELS - 1, int(idx)))
    for i in range(5):
        bit = (idx >> i) & 0x1
        GPIO.output(DAC_PINS[i], GPIO.HIGH if bit else GPIO.LOW)

def clear_dc_output():
    for pin in DAC_PINS:
        GPIO.output(pin, GPIO.LOW)

def set_digipot_wiper(step):
    step = max(0, min(OHM_MAX_STEPS, int(step)))
    GPIO.output(OHM_CS_PIN, GPIO.LOW)
    spi_meas.xfer2([0x00, step])
    GPIO.output(OHM_CS_PIN, GPIO.HIGH)

def measure_resistance():
    low, high = 0, OHM_MAX_STEPS
    matched_step = 0
    for _ in range(8):
        mid = (low + high) // 2
        set_digipot_wiper(mid)
        sleep(0.005)
        if GPIO.input(OHM_COMP_PIN) == GPIO.HIGH:
            high = mid - 1
        else:
            low = mid + 1
            matched_step = mid
    if matched_step <= 0:
        return 0.0
    if matched_step >= OHM_MAX_STEPS - 1:
        return float('inf')
    return OHM_KNOWN_R * (matched_step / (OHM_MAX_STEPS - matched_step))

def format_resistance(res):
    """Format a resistance reading with +/- 10% tolerance inline."""
    if res is None:
        return "---"
    if res == float('inf'):
        return "Out of Range"
    tol = OHM_TOL_FRACTION * res
    if res >= 1000:
        return f"{res/1000:.2f}+/-{tol/1000:.2f}k"
    return f"{res:.1f}+/-{tol:.1f}Ohm"

def measure_voltage():
    low, high = 0, VOLT_MAX_STEPS
    matched_step = 0
    for _ in range(8):
        mid = (low + high) // 2
        set_digipot_wiper(mid)
        sleep(0.005)
        if GPIO.input(VOLT_COMP_PIN) == GPIO.HIGH:
            high = mid - 1
        else:
            low = mid + 1
            matched_step = mid
    return round(_step_to_voltage(matched_step), 4)

def _step_to_voltage(step):
    if step in VOLTAGE_DATA:
        return VOLTAGE_DATA[step]
    if step < _VOLT_SORTED_STEPS[0]:
        return VOLTAGE_DATA[_VOLT_SORTED_STEPS[0]]
    if step > _VOLT_SORTED_STEPS[-1]:
        return VOLTAGE_DATA[_VOLT_SORTED_STEPS[-1]]
    low_s  = max(s for s in _VOLT_SORTED_STEPS if s < step)
    high_s = min(s for s in _VOLT_SORTED_STEPS if s > step)
    v_low, v_high = VOLTAGE_DATA[low_s], VOLTAGE_DATA[high_s]
    return v_low + (v_high - v_low) * (step - low_s) / (high_s - low_s)

def format_voltage(v):
    """Voltmeter reading with +/- 0.2 V tolerance inline."""
    if v is None:
        return "---"
    return f"{v:+.2f}+/-{VOLT_TOL_V:.1f}V"

def _rising_edge_callback(gpio, level, tick):
    global last_tick
    if level != 1:
        return
    if last_tick is not None:
        period_us = pigpio.tickDiff(last_tick, tick)
        edge_times.append(period_us)
        if len(edge_times) > FREQ_SAMPLE_CAP:
            edge_times.pop(0)
    last_tick = tick

def compute_frequency(periods_us):
    if len(periods_us) < 2:
        return None
    mean  = statistics.mean(periods_us)
    stdev = statistics.stdev(periods_us)
    filtered = [p for p in periods_us if abs(p - mean) <= 2 * stdev]
    if not filtered:
        return None
    return 1_000_000 / statistics.mean(filtered)

def start_freq_capture():
    global freq_cb, last_tick, current_frequency, current_period_ms
    global freq_sample_count
    if pi_freq is None or freq_cb is not None:
        return
    edge_times.clear()
    last_tick         = None
    current_frequency = None
    current_period_ms = None
    freq_sample_count = 0
    freq_cb = pi_freq.callback(FREQ_PIN, pigpio.RISING_EDGE,
                               _rising_edge_callback)

def stop_freq_capture():
    global freq_cb, current_frequency, current_period_ms, freq_sample_count
    if freq_cb is not None:
        try:
            freq_cb.cancel()
        except Exception:
            pass
        freq_cb = None
    current_frequency = None
    current_period_ms = None
    freq_sample_count = 0
    edge_times.clear()

def format_frequency(hz):
    """Frequency with +/- 1% tolerance inline."""
    if hz is None:
        return "---"
    tol = FREQ_TOL_FRACTION * hz
    if hz >= 1000:
        return f"{hz/1000:.2f}+/-{tol/1000:.2f}kHz"
    return f"{hz:.1f}+/-{tol:.1f}Hz"

def turn_off_all():
    global fg_output_on, dc_output_on
    fg_output_on = False
    dc_output_on = False
    apply_fg_state()
    clear_dc_output()


# ==========================================
# 5. LCD HELPERS (thread-safe, flicker-free)
# ==========================================
lcd_lock = threading.Lock()

def write_row(row, text):
    with lcd_lock:
        lcd.cursor_pos = (row, 0)
        lcd.write_string(text[:20].ljust(20))

def lcd_clear():
    with lcd_lock:
        lcd.clear()


# ==========================================
# 6. RENDERER
# ==========================================
def render_interface():
    snap_menu  = current_menu
    snap_index = menu_index

    if in_edit_mode:
        if edit_target == "FREQ":
            if fg_wave_type == "Sine":
                write_row(0, "Set Frequency (Sine)")
                write_row(1, f"  {current_freq} Hz")
                write_row(2, f"Step: {SINE_FREQ_STEP} Hz")
                write_row(3, "Click=Set Hold=Back")
            else:
                write_row(0, "Set Frequency")
                write_row(1, f"  {current_freq} Hz")
                write_row(2, "Slow:10  Fast:100")
                write_row(3, "Click=Set Hold=Back")
        elif edit_target == "AMP":
            if fg_wave_type == "Sine":
                voltage = sine_step_to_voltage(sine_amp_step)
                write_row(0, "Set Amplitude(Sine)")
                write_row(1, f"  {voltage:.2f}+/-{SINE_AMP_TOL_V:.1f}V pk")
                write_row(2, f"Step: {sine_amp_step}")
                write_row(3, "Click=Set Hold=Back")
            else:
                approx_v = (current_amp_step / 128.0) * 10.0
                write_row(0, "Set Amplitude(Sq)")
                write_row(1, f"  +/-{approx_v:.1f}+/-{SQUARE_AMP_TOL_V:.0f}V")
                write_row(2, "Spin to adjust")
                write_row(3, "Click=Set Hold=Back")
        elif edit_target == "DC_VOLT":
            write_row(0, "Set DC Voltage")
            write_row(1, f"  {dc_voltage:+.2f}+/-{DC_TOL_V:.1f}V")
            write_row(2, f"Idx:{dc_index:2d}/31 (32 lvls)")
            write_row(3, "Click=Set Hold=Back")
        return

    items     = menu_tree[snap_menu]
    num_items = len(items)
    selected  = items[snap_index]

    if snap_menu == "OHMMETER":
        write_row(0, "--- Ohmmeter ---")
        write_row(1, f"R: {format_resistance(current_resistance)}")
        write_row(2, f"> {selected}")
        write_row(3, "")
        return

    if snap_menu in ("VOLTMETER", "VOLT_SRC"):
        if volt_source == "Internal":
            write_row(0, "Internal Reference")
            write_row(1, f"Set: {dc_voltage:+.2f}+/-{DC_TOL_V:.1f}V")
            if current_voltage is None:
                write_row(2, "Read: ---")
            else:
                write_row(2, f"Read:{current_voltage:+.2f}+/-{VOLT_TOL_V:.1f}V")
        else:
            write_row(0, "External Voltmeter")
            write_row(1, f"V: {format_voltage(current_voltage)}")
            write_row(2, f"Src: {volt_source}")
        write_row(3, f"> {selected}")
        return

    if snap_menu == "FREQ_MEAS":
        write_row(0, "-- Freq Measure --")
        if pi_freq is None:
            write_row(1, "pigpio offline")
            write_row(2, "Run: sudo pigpiod")
        elif current_frequency is None:
            write_row(1, "Waiting for signal")
            write_row(2, f"GPIO {FREQ_PIN}  n={freq_sample_count}")
        else:
            write_row(1, format_frequency(current_frequency))
            write_row(2, f"T:{current_period_ms:.2f}ms n={freq_sample_count}")
        write_row(3, f"> {selected}")
        return

    if snap_menu == "FG_OUT":
        status = "ON" if fg_output_on else "OFF"
        write_row(0, f"FG Output [{status}]")
        write_row(1, f"{fg_wave_type} {current_freq}Hz")
        if fg_wave_type == "Sine":
            v = sine_step_to_voltage(sine_amp_step)
            write_row(2, f"{v:.2f}+/-{SINE_AMP_TOL_V:.1f}Vpk")
        else:
            approx_v = (current_amp_step / 128.0) * 10.0
            write_row(2, f"+/-{approx_v:.1f}+/-{SQUARE_AMP_TOL_V:.0f}V")
        write_row(3, f"> {selected}")
        return

    if snap_menu == "DC_OUT":
        status  = "ON" if dc_output_on else "OFF"
        bin_str = format(dc_index, '05b')
        write_row(0, f"DC Output [{status}]")
        write_row(1, f"Ref:{dc_voltage:+.2f}+/-{DC_TOL_V:.1f}V")
        write_row(2, f"Idx:{dc_index:2d} [{bin_str}]")
        write_row(3, f"> {selected}")
        return

    if snap_menu == "FG_TYPE":
        write_row(0, f"Type: {fg_wave_type}")
    else:
        write_row(0, f"[{snap_menu}]")

    if num_items <= 3:
        win_start = 0
    elif snap_index <= 0:
        win_start = 0
    elif snap_index >= num_items - 1:
        win_start = max(0, num_items - 3)
    else:
        win_start = snap_index - 1

    for r in range(3):
        idx = win_start + r
        if idx < num_items:
            prefix = ">" if idx == snap_index else " "
            write_row(r + 1, f"{prefix} {items[idx]}")
        else:
            write_row(r + 1, "")


# ==========================================
# 7. MENU NAVIGATION
# ==========================================
def navigate_menu(selection):
    global current_menu, menu_index, menu_history
    global fg_output_on, dc_output_on, fg_wave_type, volt_source
    global in_edit_mode, edit_target, should_exit

    if selection in ["Back", "Main", "OFF", "Exit"]:
        turn_off_all()

    if selection == "Exit":
        should_exit = True
        return

    if selection in ["Main", "OFF"]:
        current_menu = "MAIN"
        menu_index   = 0
        menu_history.clear()
    elif selection == "Back":
        if menu_history:
            current_menu = menu_history.pop()
            menu_index   = 0
    elif selection == "Mode Select":
        menu_history.append(current_menu); current_menu = "MODE_SELECT"; menu_index = 0
    elif selection == "Func Gen":
        menu_history.append(current_menu); current_menu = "FG_MENU"; menu_index = 0
    elif selection == "Ohmmeter":
        menu_history.append(current_menu); current_menu = "OHMMETER"; menu_index = 0
    elif selection == "Voltmeter":
        menu_history.append(current_menu); current_menu = "VOLTMETER"; menu_index = 0
    elif selection == "DC Ref":
        menu_history.append(current_menu); current_menu = "DC_REF"; menu_index = 0
    elif selection == "Freq Meas":
        menu_history.append(current_menu); current_menu = "FREQ_MEAS"; menu_index = 0
    elif selection == "Type":
        menu_history.append(current_menu); current_menu = "FG_TYPE"; menu_index = 0
    elif selection == "Sine":
        fg_wave_type = "Sine"
    elif selection == "Square":
        fg_wave_type = "Square"
    elif selection == "Frequency":
        menu_history.append(current_menu); current_menu = "FG_FREQ"; menu_index = 0
    elif selection == "Amplitude":
        menu_history.append(current_menu); current_menu = "FG_AMP"; menu_index = 0
    elif selection == "Output":
        menu_history.append(current_menu)
        if current_menu == "FG_MENU":
            current_menu = "FG_OUT"
        elif current_menu == "DC_REF":
            current_menu = "DC_OUT"
        menu_index = 0
    elif selection == "Source":
        menu_history.append(current_menu); current_menu = "VOLT_SRC"; menu_index = 0
    elif selection == "External":
        volt_source = "External"
    elif selection == "Internal Ref":
        volt_source = "Internal"
    elif selection == "Voltage Input":
        in_edit_mode = True; edit_target = "DC_VOLT"
    elif selection == "Input Freq":
        in_edit_mode = True; edit_target = "FREQ"
    elif selection == "Input Amp":
        in_edit_mode = True; edit_target = "AMP"
    elif selection == "On":
        if current_menu == "FG_OUT":
            fg_output_on = True;  apply_fg_state()
        elif current_menu == "DC_OUT":
            dc_output_on = True;  set_dc_output(dc_index)
    elif selection == "Off":
        if current_menu == "FG_OUT":
            fg_output_on = False; apply_fg_state()
        elif current_menu == "DC_OUT":
            dc_output_on = False; clear_dc_output()


# ==========================================
# 8. INPUT HANDLERS
# ==========================================
def handle_rotation():
    global menu_index, last_rotor_value, last_click_time, display_needs_update
    global current_freq, current_amp_step, sine_amp_step, dc_voltage, dc_index

    current_time = time()
    delta = current_time - last_click_time
    last_click_time = current_time

    new_val = rotor.steps
    diff = new_val - last_rotor_value
    if diff == 0:
        return
    last_rotor_value = new_val
    is_fast = delta < FAST_THRESHOLD

    if in_edit_mode:
        direction = 1 if diff > 0 else -1
        if edit_target == "FREQ":
            if fg_wave_type == "Sine":
                current_freq = max(SINE_FREQ_MIN,
                                   min(SINE_FREQ_MAX,
                                       current_freq + SINE_FREQ_STEP * direction))
            else:
                step = FREQ_FAST if is_fast else FREQ_SLOW
                current_freq = max(100, min(10000,
                                            current_freq + step * direction))
            if fg_output_on:
                apply_fg_state()
        elif edit_target == "AMP":
            if fg_wave_type == "Sine":
                sine_amp_step = max(SINE_STEP_MIN,
                                    min(SINE_STEP_MAX,
                                        sine_amp_step + direction))
            else:
                step = AMP_FAST if is_fast else AMP_SLOW
                current_amp_step = max(0, min(128,
                                              current_amp_step + step * direction))
            if fg_output_on:
                apply_fg_state()
        elif edit_target == "DC_VOLT":
            dc_index = max(0, min(DAC_LEVELS - 1, dc_index + direction))
            dc_voltage = index_to_dc_voltage(dc_index)
            if dc_output_on:
                set_dc_output(dc_index)
    else:
        direction = 1 if diff > 0 else -1
        max_idx = len(menu_tree[current_menu]) - 1
        menu_index = max(0, min(max_idx, menu_index + direction))

    display_needs_update = True


def handle_click():
    global in_edit_mode, edit_target, display_needs_update
    global last_release_time, ignore_next_click

    current_time = time()
    if current_time - last_release_time < 0.2:
        return
    last_release_time = current_time

    if ignore_next_click:
        ignore_next_click = False
        return

    if in_edit_mode:
        in_edit_mode = False
        edit_target  = None
        apply_fg_state()
        display_needs_update = True
        return

    selection = menu_tree[current_menu][menu_index]
    navigate_menu(selection)
    display_needs_update = True


def handle_hold():
    global current_menu, menu_index, menu_history
    global in_edit_mode, edit_target, ignore_next_click, display_needs_update

    ignore_next_click = True

    if in_edit_mode:
        in_edit_mode = False
        edit_target  = None
    else:
        turn_off_all()
        if menu_history:
            current_menu = menu_history.pop()
            menu_index   = 0
        else:
            current_menu = "MAIN"
            menu_index   = 0
            menu_history.clear()

    display_needs_update = True


# ==========================================
# 9. STARTUP & MAIN LOOP
# ==========================================
print("Testbench UI Running")
lcd_clear()
last_click_time = time()

dc_voltage = index_to_dc_voltage(dc_index)

atexit.register(stop_sine_audio)

def _signal_cleanup(signum, _frame):
    stop_sine_audio()
    sys.exit(0)

for _sig in (signal.SIGTERM, signal.SIGHUP):
    try:
        signal.signal(_sig, _signal_cleanup)
    except (ValueError, OSError):
        pass

rotor.when_rotated = handle_rotation
btn.when_released  = handle_click
btn.when_held      = handle_hold

try:
    while not should_exit:
        if display_needs_update:
            display_needs_update = False
            render_interface()

        if current_menu == "OHMMETER":
            now = time()
            if now - last_meas_time >= MEAS_INTERVAL:
                last_meas_time = now
                current_resistance = measure_resistance()
                display_needs_update = True
        elif current_menu in ("VOLTMETER", "VOLT_SRC"):
            now = time()
            if now - last_meas_time >= MEAS_INTERVAL:
                last_meas_time = now
                if volt_source == "Internal":
                    set_dc_output(dc_index)
                    sleep(0.04)
                    current_voltage = measure_voltage()
                    if not dc_output_on:
                        clear_dc_output()
                else:
                    current_voltage = measure_voltage()
                display_needs_update = True
        elif current_menu == "FREQ_MEAS":
            if freq_cb is None:
                start_freq_capture()
            now = time()
            if now - last_meas_time >= MEAS_INTERVAL:
                last_meas_time = now
                snapshot          = list(edge_times)
                freq_sample_count = len(snapshot)
                hz                = compute_frequency(snapshot)
                current_frequency = hz
                current_period_ms = (1000.0 / hz) if hz else None
                display_needs_update = True
        else:
            if current_resistance is not None:
                current_resistance = None
            if current_voltage is not None:
                current_voltage = None
            if freq_cb is not None:
                stop_freq_capture()
            last_meas_time = 0.0

        sleep(0.05)

except KeyboardInterrupt:
    pass

finally:
    turn_off_all()
    try:
        stop_sine_audio()
    except Exception:
        pass
    try:
        set_sine_wiper(128)
    except Exception:
        pass
    try:
        stop_freq_capture()
    except Exception:
        pass
    if pi_freq is not None:
        try:
            pi_freq.stop()
        except Exception:
            pass
    if pi_fg is not None:
        try:
            pi_fg.stop()
        except Exception:
            pass
    if spi_fg is not None:
        try:
            spi_fg.close()
        except Exception:
            pass
    try:
        lcd.clear()
    except Exception:
        pass
    try:
        spi_meas.close()
    except Exception:
        pass
    GPIO.cleanup()
    print("UI Ended Safely")
