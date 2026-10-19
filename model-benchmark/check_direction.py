"""Recheck v4 with classification ON in the exact same new runtime.

The shared runner disables classification for its v4 control. This narrow wrapper
forces that one constructor flag ON and records the effective flag in run-info.
"""
import sys
import rapidocr_onnxruntime
import run_models
Original=rapidocr_onnxruntime.RapidOCR
class ClassifiedV4(Original):
    def __init__(self,*args,**kwargs):
        kwargs['use_cls']=True
        super().__init__(*args,**kwargs)
        assert self.use_cls is True
        obj=self.text_cls;wrapper=obj.infer if hasattr(obj,'infer') else obj.session
        assert 'CUDAExecutionProvider' in wrapper.session.get_providers()
        print('Controlled v4 classification ON confirmed; CUDA provider available.',flush=True)
rapidocr_onnxruntime.RapidOCR=ClassifiedV4
run_models.MODELS['A_v4_cls_recheck']=None
run_models.PARAMS['use_textline_orientation']=True
sys.argv=[sys.argv[0],'--variant','A_v4_cls_recheck']
run_models.main()
