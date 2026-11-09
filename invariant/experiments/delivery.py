"""Frozen host-delivery rules, independent of complete continuation snapshots.

Ignore files are read ONLY from the immutable baseline. A worker cannot expand
its host write scope by editing .gitignore. No Git executable or repository is
required. Explicit include/exclude rules use the recipe's POSIX glob grammar.
"""
from pathlib import PurePosixPath
import re

from .snapshots import glob_match, regular_bytes
from .errors import ExperimentError


def wildmatch(pattern):
    parts=[];i=0
    while i<len(pattern):
        c=pattern[i]
        if c=='\\' and i+1<len(pattern):
            i+=1;parts.append(re.escape(pattern[i]))
        elif pattern[i:i+3]=='**/':parts.append('(?:.*/)?');i+=2
        elif pattern[i:i+2]=='**':parts.append('.*');i+=1
        elif c=='*':parts.append('[^/]*')
        elif c=='?':parts.append('[^/]')
        elif c=='[':
            end=pattern.find(']',i+1)
            if end==-1:parts.append(r'\[')
            else:
                chars=pattern[i+1:end]
                if chars.startswith('!'):chars='^'+chars[1:]
                parts.append('(?=[^/])['+chars.replace('\\','\\\\')+']');i=end
        else:parts.append(re.escape(c))
        i+=1
    try:return re.compile('^'+''.join(parts)+'$')
    except re.error as error:raise ExperimentError('INVALID_APPLY_POLICY','Invalid frozen ignore pattern: '+pattern) from error


class DeliveryPolicy:
    @staticmethod
    def freeze(policy,baseline,entries):
        files={}
        if policy.get('respect_gitignore',False):
            for path,row in entries.items():
                if PurePosixPath(path).name!='.gitignore' or row['type']!='file' or path.startswith('@input/'):continue
                raw,_=regular_bytes(baseline,path,maximum=1024*1024)
                try:files[path]=raw.decode('utf-8')
                except UnicodeError:raise ExperimentError('INVALID_APPLY_POLICY','Ignore file must be UTF-8: '+path) from None
        return {'policy':policy,'ignore_files':files}

    def __init__(self, policy, baseline, entries, *, frozen=None, result_entries=None):
        frozen=self.freeze(policy,baseline,entries) if frozen is None else frozen
        self.policy=frozen['policy'];self.groups=[];self.cache={}
        for path,content in sorted(frozen['ignore_files'].items(),key=lambda item:(item[0].count('/'),item[0])):
            lines=content.splitlines()
            rules=[]
            for line in lines:
                # Git ignores unescaped trailing spaces.
                line=re.sub(r'(?<!\\) +$','',line)
                if not line or line.startswith('#'):continue
                negated=line.startswith('!')
                if negated:line=line[1:]
                directory=line.endswith('/')
                if directory:line=line[:-1]
                anchored=line.startswith('/') or '/' in line
                line=line.lstrip('/')
                if line:rules.append((wildmatch(line),negated,directory,anchored))
            parent=str(PurePosixPath(path).parent)
            self.groups.append(('' if parent=='.' else parent+'/',rules))
        self.structural=set()
        for path,row in {**entries,**(result_entries or {})}.items():
            if not self._excluded(path,row):
                self.structural.update(p.as_posix() for p in PurePosixPath(path).parents if str(p)!='.')

    def ignored(self,path,is_directory=False):
        key=(path,is_directory)
        if key in self.cache:return self.cache[key]
        parent=str(PurePosixPath(path).parent)
        if parent!='.' and self.ignored(parent,True):return True
        ignored=False
        for prefix,rules in self.groups:
            if not path.startswith(prefix):continue
            relative=path[len(prefix):]
            for regex,negated,directory,anchored in rules:
                candidate=relative if anchored else relative.rsplit('/',1)[-1]
                if (not directory or is_directory) and regex.fullmatch(candidate):ignored=not negated
        self.cache[key]=ignored
        return ignored

    def _excluded(self,path,row=None,*,structural=False):
        # Dependencies and agent state are never delivery artifacts, even when a
        # worker changes ignore rules or the caller supplies a broad include.
        from ..docker_storage import EXCLUDED
        if any(part in EXCLUDED or part.startswith(('.invariant-import-','.invariant-apply-')) for part in PurePosixPath(path).parts):return True
        candidates=[path,*(p.as_posix() for p in PurePosixPath(path).parents if str(p)!='.')]
        if any(glob_match(p,r) or glob_match(p+'/',r) for p in candidates for r in self.policy.get('exclude',[])):return True
        include=self.policy.get('include',['**/*'])
        if not structural and not any(glob_match(path,r) or row and row['type']=='directory' and glob_match(path+'/',r) for r in include):return True
        return self.ignored(path,bool(row and row['type']=='directory'))

    def excluded(self,path,row=None):
        return self._excluded(path,row,structural=bool(row and row['type']=='directory' and path in self.structural))
