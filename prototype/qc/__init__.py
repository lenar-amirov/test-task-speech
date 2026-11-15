"""Auto-QC for DataTasks speech recordings: signal checks + speech recognition against the prompt."""
from .signal import analyze_signal
from .checks import Thresholds, check_recording

__all__ = ["analyze_signal", "Thresholds", "check_recording"]
