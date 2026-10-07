import tempfile, unittest
from pathlib import Path
from canvas_sync.store import Store


class TempStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "archive"
        self.store = Store(self.root)

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()
