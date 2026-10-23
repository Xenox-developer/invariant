import test from 'node:test';
import assert from 'node:assert/strict';
import {slugify} from '../slug.mjs';

test('ASCII words become lowercase dash-separated slugs', () => {
  for (const [value, expected] of [['Hello World','hello-world'],['  many   spaces  ','many-spaces'],
                                 ['A__B--C','a-b-c'],['123','123'],['!!!',''],['','']]) {
    assert.equal(slugify(value), expected);
  }
});
test('other types are rejected', () => {
  for (const value of [null, 1, [], {}]) assert.throws(() => slugify(value), TypeError);
});
