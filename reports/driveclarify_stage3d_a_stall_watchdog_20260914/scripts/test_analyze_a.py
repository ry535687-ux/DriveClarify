import unittest
from analyze_a import lag_rows, route_summary, target_from_record


class ParserChecks(unittest.TestCase):
    def test_native_index_contract(self):
        points = [[0.,0.] for _ in range(10)]
        points[2] = [3.,4.]
        points[9] = [1000.,1000.]
        self.assertEqual(target_from_record([points]), 10.)

    def test_missing_not_zero(self):
        self.assertIsNone(target_from_record(None))
        self.assertIsNone(route_summary(None))

    def test_invalid_rejected(self):
        for value in [[], [[[0.,0.]]], [[[float('nan'),0.]]*10]]:
            with self.assertRaises(ValueError): target_from_record(value)

    def test_raw_geometry(self):
        s=route_summary([[[float(i),0.] for i in range(20)]])
        self.assertEqual(s['path_length_raw'],19.)
        self.assertTrue(s['first_axis_strictly_increasing'])
        self.assertFalse(route_summary([[[0.,0.]]*20])['first_axis_strictly_increasing'])

    def test_lag_does_not_bridge_missing_frames(self):
        a={'frame':10,'sim_time':1.,'ego_speed_if_recorded':None,'ego_x':0.,'ego_y':0.,'throttle':1.,'brake':0.}
        b={**a,'frame':11,'sim_time':1.05,'ego_x':.1}
        self.assertIsNone(lag_rows([a,b],1)[0]['speed_delta_mps'])
        self.assertAlmostEqual(lag_rows([a,b],1)[0]['xy_displacement_m'],.1)
        self.assertEqual(lag_rows([a,{**b,'frame':12}],1),[])


if __name__=='__main__': unittest.main()
