"""Validated conservative text-line orientation protection."""
import cv2

class GuardClassifier:
    def __init__(self,original):self.original=original;self.angles=[]
    def __call__(self,crops):
        _,self.angles,elapsed=self.original(crops)
        return crops,self.angles,elapsed
    def __getattr__(self,key):return getattr(self.original,key)
class GuardRecognizer:
    def __init__(self,original,classifier):self.original=original;self.classifier=classifier;self.stats={}
    def __call__(self,crops,return_word_box=False):
        if return_word_box:raise ValueError('guarded mode does not support word boxes')
        initial,elapsed=self.original(crops,False)
        candidates=[i for i,r in enumerate(initial) if r[1]<.5 or (i<len(self.classifier.angles) and self.classifier.angles[i][0]=='180' and self.classifier.angles[i][1]>.9)]
        chosen=0;output=list(initial)
        if candidates:
            alternatives,extra=self.original([cv2.rotate(crops[i],cv2.ROTATE_180) for i in candidates],False);elapsed+=extra
            for i,alt in zip(candidates,alternatives):
                if alt[1]>initial[i][1]+.02:output[i]=alt;chosen+=1
        self.stats={'lines_tested_180':len(candidates),'lines_chosen_180':chosen,'recognized_lines':len(initial)}
        return output,elapsed
    def __getattr__(self,key):return getattr(self.original,key)

