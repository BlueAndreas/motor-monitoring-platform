"""检查数据编码及关键异常场景，不需要联网。"""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from motor_simulator import build_sample, to_registers, from_registers


class SampleTests(unittest.TestCase):
    def test_register_scaling(self):
        sample = {"winding_temp_c": 73.5, "bearing_temp_c": 45.2, "current_a": 8.2,
                  "speed_rpm": 1480, "running": 1, "sample_seq": 65535}
        self.assertEqual(to_registers(sample), [735, 452, 82, 1480, 1, 65535])
        self.assertEqual(from_registers([735, 452, 82, 1480, 1, 65535]), sample)

    def test_fault_scenarios(self):
        self.assertGreaterEqual(build_sample(20, 20, "overtemp")["winding_temp_c"], 80)
        self.assertGreaterEqual(build_sample(20, 20, "overcurrent")["current_a"], 15)

    def test_short_spike_and_recovery(self):
        self.assertLess(build_sample(4, 4, "spike")["winding_temp_c"], 80)
        self.assertGreaterEqual(build_sample(6, 6, "spike")["winding_temp_c"], 80)
        self.assertLess(build_sample(8, 8, "spike")["winding_temp_c"], 75)

    def test_freeze_and_counter_wrap(self):
        self.assertEqual(build_sample(0, 0, "freeze"), build_sample(30, 30, "freeze"))
        self.assertEqual(build_sample(1, 65536, "normal")["sample_seq"], 0)


if __name__ == "__main__":
    unittest.main()
