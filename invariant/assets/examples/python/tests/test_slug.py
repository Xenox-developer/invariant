import unittest
from slug import slugify


class SlugTests(unittest.TestCase):
    def test_ascii_words_are_lowercase_and_separated(self):
        for value,expected in [('Hello World','hello-world'),('  many   spaces  ','many-spaces'),
                               ('A__B--C','a-b-c'),('123','123'),('!!!',''),('','')]:
            with self.subTest(value=value):self.assertEqual(slugify(value),expected)

    def test_other_types_are_rejected(self):
        for value in [None,1,[],{}]:
            with self.subTest(value=value),self.assertRaises(TypeError):slugify(value)
