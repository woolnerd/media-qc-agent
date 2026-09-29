import unittest

from media_qc_agent.cli.demo import run_demo


class DemoTests(unittest.TestCase):
    def test_demo_exercises_stale_callback_and_lineage(self) -> None:
        result = run_demo()

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["old_event"], "stale")
        self.assertEqual(result["new_event"], "applied")
        self.assertEqual(result["caption_source_video"], result["active_video"])
        self.assertEqual(
            result["video_sources"],
            {
                "script": "script-1",
                "tts_input": "tts-1",
                "avatar": "avatar-1",
                "voice": "voice-1",
            },
        )


if __name__ == "__main__":
    unittest.main()
