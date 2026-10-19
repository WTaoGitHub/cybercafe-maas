import unittest
import numpy as np
from run_variants import starts,merge,recognize
class VariantTests(unittest.TestCase):
    def test_tile_coverage(self):
        for n in [1,1024,1025,2400,3200,5000]:
            xs=starts(n,1024,192)
            self.assertEqual(xs[0],0)
            self.assertGreaterEqual(xs[-1]+1024,n)
            self.assertTrue(all(b<=a+1024 for a,b in zip(xs,xs[1:])))
    def test_spatial_dedup(self):
        a={'box':[[0,0],[100,0],[100,20],[0,20]],'text':'ABC','score':.9}
        self.assertEqual(len(merge([a,dict(a,score=.8)])),1)
        other=dict(a,box=[[200,0],[300,0],[300,20],[200,20]])
        self.assertEqual(len(merge([a,other])),2)
    def test_rotation_coordinates(self):
        def engine(image):return [([[2,10],[8,10],[8,20],[2,20]],'X',.9)],None
        r=recognize(engine,np.zeros((50,100,3),dtype=np.uint8),{'rotate90':True})
        self.assertEqual(r['lines'][0]['box'],[[89.,2.],[89.,8.],[79.,8.],[79.,2.]])
    def test_tile_offsets(self):
        def engine(image):return [([[0,0],[10,0],[10,10],[0,10]],'X',.9)],None
        r=recognize(engine,np.zeros((10,1600,3),dtype=np.uint8),{'tile_size':1024,'tile_overlap':192})
        self.assertEqual(r['engine_calls'],2)
        self.assertEqual(r['lines'][1]['box'][0],[576.,0.])
if __name__=='__main__':unittest.main()
