import unittest
import numpy as np
from vision.camera import _frame_to_bgr, _select_color_profile


class Format:
    RGB='RGB'; BGR='BGR'; MJPG='MJPG'; YUYV='YUYV'; UYVY='UYVY'; NV12='NV12'; NV21='NV21'; I420='I420'
class Ob: OBFormat=Format
class Frame:
    def __init__(self, data, fmt, width=2, height=1): self.data=data; self.fmt=fmt; self.width=width; self.height=height
    def get_data(self): return self.data
    def get_format(self): return self.fmt
    def get_width(self): return self.width
    def get_height(self): return self.height
class Profile:
    def __init__(self,w,h,fps,fmt): self.v=(w,h,fps,fmt)
    def as_video_stream_profile(self): return self
    def get_width(self): return self.v[0]
    def get_height(self): return self.v[1]
    def get_fps(self): return self.v[2]
    def get_format(self): return self.v[3]
class Profiles:
    def __init__(self, ps): self.ps=ps
    def get_count(self): return len(self.ps)
    def get_stream_profile_by_index(self,i): return self.ps[i]

class CameraAdapterTests(unittest.TestCase):
    def test_rgb_and_bgr_conversion(self):
        rgb=np.array([255,0,0, 0,255,0],dtype=np.uint8)
        np.testing.assert_array_equal(_frame_to_bgr(Frame(rgb,Format.RGB),Ob)[0], [[0,0,255],[0,255,0]])
        bgr=np.array([1,2,3, 4,5,6],dtype=np.uint8)
        np.testing.assert_array_equal(_frame_to_bgr(Frame(bgr,Format.BGR),Ob)[0], [[1,2,3],[4,5,6]])
    def test_profile_selection_prefers_rgb(self):
        ps=Profiles([Profile(640,480,30,Format.MJPG),Profile(640,480,30,Format.RGB)])
        self.assertEqual(_select_color_profile(ps,640,480,30,Ob).get_format(),Format.RGB)
        with self.assertRaisesRegex(RuntimeError,'Available'):
            _select_color_profile(ps,1280,720,30,Ob)

if __name__=='__main__': unittest.main()
