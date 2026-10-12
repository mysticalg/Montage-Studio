import unittest
from unittest.mock import patch
from types import SimpleNamespace
from beat_sync import detect_beats,beat_timeline,fit_lengths

class BeatTests(unittest.TestCase):
 def test_silent_audio_is_rejected_with_manual_tempo_hint(self):
  with patch('beat_sync.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=b'\x00\x00'*16000,stderr=b'')):
   with self.assertRaisesRegex(ValueError,'Enter the track BPM'):detect_beats('silence.wav',2)
 def test_manual_bpm_and_invalid_value(self):
  self.assertEqual(detect_beats('not-read.wav',2.1,120)['times'],[.5,1,1.5,2])
  for bpm in (20,300,float('nan')):
   with self.assertRaises(ValueError):detect_beats('not-read.wav',3,bpm)
 def test_caps_and_crossfade_centres_on_beats(self):
  p=beat_timeline([4,2,4],[2,2,2],5,[.5*i for i in range(1,10)],30,.5)
  self.assertEqual(len(p['lengths']),3)
  self.assertTrue(all(a<=b for a,b in zip(p['lengths'],[4,2,4])))
  self.assertAlmostEqual(sum(p['lengths'])-sum(p['overlaps']),5)
  self.assertTrue(all(c in [.5*i for i in range(1,10)] for c in p['cuts']))
 def test_impossible_grid_rejected_instead_of_repeating_or_unsynced_cut(self):
  with self.assertRaises(ValueError):beat_timeline([.3,.3],[.3,.3],.6,[.5],30)
  with self.assertRaisesRegex(ValueError,'Not enough beats'):beat_timeline([2]*5,[1]*5,5,[1,2],30)
 def test_beat_only_can_shorten_total_for_off_phase_sources(self):
  p=beat_timeline([2,2,2],[2,2,2],6,[.25+i*.5 for i in range(12)],30,flexible_end=True)
  self.assertEqual(len(p['lengths']),3);self.assertLess(p['duration'],6)
  self.assertTrue(all(length<=2 for length in p['lengths']))
  self.assertTrue(all(min(abs(cut-(.25+i*.5)) for i in range(12))<=1/60 for cut in p['cuts']))

if __name__=='__main__':unittest.main()
