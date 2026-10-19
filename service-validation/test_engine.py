import sys,unittest
sys.path.insert(0,'/app')
import numpy as np
from engine import rotate_points,rotated_size,rotate_image,GuardClassifier,GuardRecognizer
class DirectionTests(unittest.TestCase):
    def test_rotation_inverse(self):
        points=[[0,0],[9,2],[11,7],[1,8]]
        for angle in (0,90,180,270):
            w,h=rotated_size(12,9,angle)
            result=rotate_points(rotate_points(points,12,9,angle),w,h,(-angle)%360)
            np.testing.assert_allclose(result,points)
            image=np.arange(9*12*3).reshape(9,12,3)
            np.testing.assert_array_equal(rotate_image(rotate_image(image,angle),(-angle)%360),image)
    def test_classifier_keeps_original(self):
        image=np.arange(12).reshape(2,2,3);original=lambda crops:([np.rot90(crops[0],2)],[['180',.99]],.01)
        wrapper=GuardClassifier(original);out,angles,_=wrapper([image]);np.testing.assert_array_equal(out[0],image);self.assertEqual(angles[0][0],'180')
    def test_guard_does_not_drop_correct_upright(self):
        results=iter([([('UPRIGHT',.99)],.01),([('wrong',.10)],.01)])
        classifier=type('C',(),{'angles':[['180',.99]]})();guard=GuardRecognizer(lambda *args:next(results),classifier)
        out,_=guard([np.zeros((2,2,3),np.uint8)]);self.assertEqual(out[0][0],'UPRIGHT');self.assertEqual(guard.stats['lines_chosen_180'],0)
    def test_guard_recovers_inverted_low_score(self):
        results=iter([([('bad',.10)],.01),([('RECOVERED',.99)],.01)])
        classifier=type('C',(),{'angles':[['0',.99]]})();guard=GuardRecognizer(lambda *args:next(results),classifier)
        out,_=guard([np.zeros((2,2,3),np.uint8)]);self.assertEqual(out[0][0],'RECOVERED');self.assertEqual(guard.stats['lines_chosen_180'],1)
if __name__=='__main__':unittest.main()
