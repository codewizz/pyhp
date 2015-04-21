from tests import TestBase

class TestMain(TestBase):
    def test_if(self, capfd):
        out = self.run("""
        $x = 1;
        if ($x >= 1) {
            print $x;
        }""", capfd)
        assert out == "1"

    def test_if_equal(self, capfd):
        out = self.run("""
        $x = 1;
        if ($x == 1) {
            print $x;
        }""", capfd)
        assert out == "1"

    def test_while(self, capfd):
        out = self.run("""
        $x = 1;
        while ($x >= 1) {
            print $x;
            $x = $x - 1;
        }""", capfd)
        assert out == "1"

    def test_while_lt(self, capfd):
        out = self.run("""
        $x = 1;
        while ($x < 10) {
            print $x;
            $x = $x + 1;
        }""", capfd)
        assert out == "123456789"

    def test_for(self, capfd):
        out = self.run("""
        for ($x = 1; $x < 10; $x++) {
            print $x;
        }""", capfd)
        assert out == "123456789"
