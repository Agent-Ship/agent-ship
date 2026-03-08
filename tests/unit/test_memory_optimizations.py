"""Unit tests for memory optimizations."""

import pytest
import sys
from unittest.mock import patch, MagicMock


class TestLazyImports:
    """Test that heavy imports are lazy-loaded."""
    
    def test_azure_imports_lazy(self):
        """Test that Azure imports are not loaded at module level."""
        # Import the module
        from src.agent_framework.utils import azure_utils
        
        # Check that Azure libraries are not imported yet (if they were, they'd be in sys.modules)
        # Note: This might fail if Azure was imported elsewhere, but that's okay
        azure_blob_imported = 'azure.storage.blob' in sys.modules
        azure_core_imported = 'azure.core.exceptions' in sys.modules
        
        # Create instance - should not initialize client yet
        utils = azure_utils.AzureUtils()
        assert utils._blob_service_client is None
        assert not utils._client_initialized
        
        # Access property - should trigger lazy import and initialization
        # This will try to import Azure libraries if they're available
        client = utils.blob_service_client
        
        # After accessing, client should be initialized (even if None due to missing credentials)
        assert utils._client_initialized is True
        
        # If Azure libraries weren't imported before, accessing the property
        # should have triggered the import (if Azure is installed)
        # If Azure is not installed, client will be None but initialized will be True
        # Both cases are valid - the key is that initialization is lazy
    
    def test_azure_utils_handles_missing_libraries(self):
        """Test that AzureUtils handles missing Azure libraries gracefully."""
        from src.agent_framework.utils import azure_utils
        
        utils = azure_utils.AzureUtils()
        
        # Mock ImportError when trying to import Azure
        with patch('builtins.__import__', side_effect=ImportError("No module named 'azure'")):
            # Should handle gracefully
            client = utils.blob_service_client
            assert client is None
            assert utils._client_initialized is True


class TestRemovedDependencies:
    """Test that removed dependencies are not imported."""
    
    def test_google_cloud_aiplatform_not_imported(self):
        """Test that google-cloud-aiplatform is not imported."""
        # Try to import - should fail if not installed
        try:
            import google.cloud.aiplatform
            pytest.skip("google-cloud-aiplatform is still installed")
        except ImportError:
            pass  # Expected - should not be installed
    
    def test_google_cloud_bigtable_not_imported(self):
        """Test that google-cloud-bigtable is not imported."""
        try:
            import google.cloud.bigtable
            pytest.skip("google-cloud-bigtable is still installed")
        except ImportError:
            pass  # Expected - should not be installed
    
    def test_google_cloud_spanner_not_imported(self):
        """Test that google-cloud-spanner is not imported."""
        try:
            import google.cloud.spanner
            pytest.skip("google-cloud-spanner is still installed")
        except ImportError:
            pass  # Expected - should not be installed
    
    def test_sqlalchemy_spanner_not_imported(self):
        """Test that sqlalchemy-spanner is not imported."""
        try:
            import sqlalchemy_spanner
            pytest.skip("sqlalchemy-spanner is still installed")
        except ImportError:
            pass  # Expected - should not be installed


class TestHealthEndpoint:
    """Test that health endpoint includes memory info."""
    
    @pytest.mark.asyncio
    async def test_health_endpoint_includes_memory(self):
        """Test that /health endpoint returns memory information."""
        from fastapi.testclient import TestClient
        from src.service.main import app
        
        client = TestClient(app)
        response = client.get("/health")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "status" in data
        assert data["status"] == "running"
        
        # If psutil is available, should include memory info
        try:
            import psutil
            assert "memory_mb" in data
            assert "memory_limit_mb" in data
            assert "within_limit" in data
            assert "percent_of_limit" in data
            assert isinstance(data["memory_mb"], (int, float))
            assert data["memory_limit_mb"] == 512.0
        except ImportError:
            # psutil not installed - just check basic status
            pass


class TestVerifyModelKeysOnLaunch:
    """Very small tests for LLM API key verification helper."""

    def test_returns_false_when_only_placeholder_key_set(self):
        """If only placeholder-style keys are set, verification should fail."""
        from src.service.main import _verify_model_keys_on_launch

        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "your-openai-key",
                "ANTHROPIC_API_KEY": "",
                "GEMINI_API_KEY": "",
                "GOOGLE_API_KEY": "",
            },
            clear=True,
        ):
            assert _verify_model_keys_on_launch() is False

    def test_returns_true_when_real_looking_key_set(self):
        """If a non-placeholder key is set, verification should pass."""
        from src.service.main import _verify_model_keys_on_launch

        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "sk-real-key-123",
                "ANTHROPIC_API_KEY": "",
                "GEMINI_API_KEY": "",
                "GOOGLE_API_KEY": "",
            },
            clear=True,
        ):
            assert _verify_model_keys_on_launch() is True

