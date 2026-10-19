import unittest
from evaluate_ocr import score,normalize

def box(x,y,w,h):return [[x,y],[x+w,y],[x+w,y+h],[x,y+h]]
class MetricTests(unittest.TestCase):
    def test_split_line_and_ignored(self):
        a={'regions':[{'polygon':box(0,0,100,10),'text':'AB','ignore':False},{'polygon':box(0,20,100,10),'text':'###','ignore':True}]}
        r={'lines':[{'box':box(50,0,50,10),'text':'B'},{'box':box(0,0,50,10),'text':'A'},{'box':box(0,20,100,10),'text':'noise'}]}
        s=score(a,r);self.assertEqual(s['normalized_edits'],0);self.assertEqual(s['ignored_predictions'],1)
    def test_merge_missing_and_extra(self):
        a={'regions':[{'polygon':box(0,0,50,10),'text':'A','ignore':False},{'polygon':box(50,0,50,10),'text':'B','ignore':False},{'polygon':box(0,40,20,10),'text':'C','ignore':False}]}
        r={'lines':[{'box':box(0,0,100,10),'text':'AB'},{'box':box(0,80,20,10),'text':'X'}]}
        s=score(a,r);self.assertEqual(s['normalized_edits'],2);self.assertEqual(s['missing_regions'],1);self.assertEqual(s['unmatched_predictions'],1)
    def test_zero_area_reference(self):
        s=score({'regions':[{'polygon':box(0,0,10,0),'text':'X','ignore':False}]},{'lines':[]})
        self.assertEqual(s['invalid_reference_regions'],1);self.assertEqual(s['reference_regions'],0)
    def test_normalization(self):self.assertEqual(normalize('Ａ b\n中。'),'ab中。')
if __name__=='__main__':unittest.main()
