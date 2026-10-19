import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from summarize_load import energy,cost_per1000
class EnergyTests(unittest.TestCase):
 def rows(self):return [{'unix_time':0.,'power_w':100.,'gpu_util_pct':20.,'memory_mib':1000.},{'unix_time':3600.,'power_w':100.,'gpu_util_pct':20.,'memory_mib':1000.}]
 def test_one_hour_constant_power(self):self.assertAlmostEqual(energy(self.rows(),0,3600)['energy_wh'],100)
 def test_partial_linear_interpolation(self):
  rows=[{'unix_time':0.,'power_w':0.,'gpu_util_pct':0.,'memory_mib':1.},{'unix_time':10.,'power_w':100.,'gpu_util_pct':100.,'memory_mib':1.}]
  self.assertAlmostEqual(energy(rows,2.5,7.5)['energy_wh'],250/3600)
 def test_currency_per_thousand(self):
  # 100W, 10 images/s => 100 seconds per1000 => 0.002777...kWh.
  measured_wh=100*4/3600
  self.assertAlmostEqual(cost_per1000(measured_wh,40,1),100*100/3600/1000)
 def test_incomplete_telemetry_rejected(self):
  with self.assertRaises(ValueError):energy(self.rows(),-1,100)
if __name__=='__main__':unittest.main()
