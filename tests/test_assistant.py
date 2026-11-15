import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# S'assurer que le dossier racine et les packages internes sont dans le path
ROOT_DIR = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "packages" / "core"))
sys.path.insert(0, str(ROOT_DIR / "packages" / "shared"))


class TestAssistantHardware(unittest.TestCase):
    @patch("core.assistant.genai.Client")
    @patch("core.assistant.cv2.VideoCapture")
    @patch("sounddevice.query_devices")
    def test_hardware_available(self, mock_query_devices, mock_video_capture, mock_genai_client):
        # Setup mock camera
        mock_camera = MagicMock()
        mock_camera.isOpened.return_value = True
        mock_video_capture.return_value = mock_camera

        # Setup mock sounddevice
        mock_query_devices.return_value = [
            {"name": "Default Speaker", "max_input_channels": 0},
            {"name": "Default Microphone", "max_input_channels": 1},
        ]

        from core.assistant import Assistant

        assistant = Assistant()

        self.assertTrue(assistant.is_camera_available)
        self.assertTrue(assistant.is_mic_available)
        self.assertIn("CAMÉRA DISPONIBLE", assistant._enhanced_prompt)
        self.assertIn("MICROPHONE DISPONIBLE", assistant._enhanced_prompt)
        self.assertNotIn("CAMÉRA NON DISPONIBLE", assistant._enhanced_prompt)
        self.assertNotIn("MICROPHONE NON DISPONIBLE", assistant._enhanced_prompt)

    @patch("core.assistant.genai.Client")
    @patch("core.assistant.cv2.VideoCapture")
    @patch("sounddevice.query_devices")
    def test_hardware_unavailable(self, mock_query_devices, mock_video_capture, mock_genai_client):
        # Setup mock camera failing to open
        mock_camera = MagicMock()
        mock_camera.isOpened.return_value = False
        mock_video_capture.return_value = mock_camera

        # Setup mock sounddevice with no input channels
        mock_query_devices.return_value = [{"name": "Default Speaker", "max_input_channels": 0}]

        from core.assistant import Assistant

        assistant = Assistant()

        self.assertFalse(assistant.is_camera_available)
        self.assertFalse(assistant.is_mic_available)
        self.assertIn("CAMÉRA NON DISPONIBLE", assistant._enhanced_prompt)
        self.assertIn("MICROPHONE NON DISPONIBLE", assistant._enhanced_prompt)
        self.assertNotIn("CAMÉRA DISPONIBLE", assistant._enhanced_prompt)
        self.assertNotIn("MICROPHONE DISPONIBLE", assistant._enhanced_prompt)

    @patch("core.assistant.GOOGLE_CLOUD_LOCATION", "europe-west1")
    @patch("core.assistant.GOOGLE_CLOUD_PROJECT", "my-gcp-project")
    @patch("core.assistant.GOOGLE_GENAI_USE_VERTEXAI", True)
    @patch("core.assistant.genai.Client")
    @patch("core.assistant.cv2.VideoCapture")
    @patch("sounddevice.query_devices")
    def test_assistant_vertexai_init(
        self, mock_query_devices, mock_video_capture, mock_genai_client
    ):
        mock_camera = MagicMock()
        mock_camera.isOpened.return_value = False
        mock_video_capture.return_value = mock_camera
        mock_query_devices.return_value = []

        from core.assistant import Assistant

        Assistant()

        mock_genai_client.assert_called_once_with(
            vertexai=True,
            project="my-gcp-project",
            location="europe-west1",
        )


class TestGeminiProviderVertexAI(unittest.TestCase):
    @patch("google.genai.Client")
    def test_gemini_provider_vertexai_client(self, mock_genai_client):
        from omi_core.llm.gemini import GeminiProvider

        provider = GeminiProvider(
            vertexai=True,
            project="my-gcp-project",
            location="us-central1",
        )
        provider._get_client()

        mock_genai_client.assert_called_once_with(
            vertexai=True,
            project="my-gcp-project",
            location="us-central1",
        )

    @patch("google.genai.Client")
    def test_gemini_provider_vertexai_express_key(self, mock_genai_client):
        from omi_core.llm.gemini import GeminiProvider

        provider = GeminiProvider(api_key="AQ.test_vertex_key")
        provider._get_client()

        mock_genai_client.assert_called_once_with(
            vertexai=True,
            api_key="AQ.test_vertex_key",
        )
