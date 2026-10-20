"""No-follow destination reads and atomic per-entry writes through directory descriptors."""
import errno
import hashlib
import os
from pathlib import PurePosixPath
import stat

from .errors import ExperimentError
from .snapshots import safe_path, fingerprint, regular_bytes


def identity(path):
    value=os.stat(path,follow_symlinks=False)
    if not stat.S_ISDIR(value.st_mode):raise ExperimentError('UNSAFE_DESTINATION','Project root is not a directory.')
    return {'device':value.st_dev,'inode':value.st_ino}


def entry(project,path,*,children=False):
    try:fd,name=safe_path(project,path)
    except OSError as error:
        if error.errno in (errno.ENOENT,errno.ENOTDIR,errno.ELOOP):return None
        raise
    try:
        try:info=os.stat(name,dir_fd=fd,follow_symlinks=False)
        except FileNotFoundError:return None
        if stat.S_ISLNK(info.st_mode):return {'type':'symlink','target':os.readlink(name,dir_fd=fd)}
        if stat.S_ISDIR(info.st_mode):
            value={'type':'directory','mode':info.st_mode&0o777}
            if children:
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                try:value['children']=sorted(os.listdir(child))
                finally:os.close(child)
            return value
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
            raise ExperimentError('UNSAFE_DESTINATION','Destination is a special or hard-linked file: '+path)
        source=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        with os.fdopen(source,'rb') as stream:
            before=os.fstat(stream.fileno())
            if fingerprint(before)!=fingerprint(info):raise ExperimentError('SOURCE_CHANGED','Destination changed while reading: '+path)
            sha=hashlib.sha256();size=0
            while True:
                data=stream.read(1024*1024)
                if not data:break
                sha.update(data);size+=len(data)
            if fingerprint(before)!=fingerprint(os.fstat(stream.fileno())):
                raise ExperimentError('SOURCE_CHANGED','Destination changed while reading: '+path)
        return {'type':'file','mode':info.st_mode&0o777,'size':size,'sha256':sha.hexdigest()}
    finally:os.close(fd)


def set_entry(project,path,before,after,blob,temporary,root_identity):
    """All replacements are checked again after staging, immediately before mutation."""
    fd,name=safe_path(project,path);temp=PurePosixPath(temporary).name
    created=None
    try:
        if identity(project)!=root_identity:raise ExperimentError('STALE_PLAN','Project directory identity changed.')
        if after and after['type'] in ('file','symlink'):
            if after['type']=='file':
                data,_=regular_bytes(blob.parent,blob.name,maximum=after['size'])
                if len(data)!=after['size'] or hashlib.sha256(data).hexdigest()!=after['sha256']:
                    raise ExperimentError('CORRUPT_PLAN','Staged content changed.')
                stage=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
                created=os.fstat(stage)
                with os.fdopen(stage,'wb') as output:
                    output.write(data);output.flush();os.fchmod(output.fileno(),after['mode']);os.fsync(output.fileno())
            else:
                os.symlink(after['target'],temp,dir_fd=fd);created=os.stat(temp,dir_fd=fd,follow_symlinks=False)
            os.fsync(fd)
        fresh,unused=safe_path(project,path)
        try:
            if (os.fstat(fresh).st_dev,os.fstat(fresh).st_ino)!=(os.fstat(fd).st_dev,os.fstat(fd).st_ino):
                raise ExperimentError('STALE_PLAN','Destination directory moved: '+path)
        finally:os.close(fresh)
        if entry(project,path)!=before:raise ExperimentError('DESTINATION_CHANGED','Destination changed immediately before replacement: '+path)
        if after is None:
            if before['type']=='directory':os.rmdir(name,dir_fd=fd)
            else:os.unlink(name,dir_fd=fd)
        elif after['type']=='directory':
            if before is None:os.mkdir(name,0o700,dir_fd=fd)
            child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            try:os.fchmod(child,after['mode']);os.fsync(child)
            finally:os.close(child)
        else:
            os.replace(temp,name,src_dir_fd=fd,dst_dir_fd=fd);created=None
        os.fsync(fd)
    finally:
        if created is not None:
            try:
                current=os.stat(temp,dir_fd=fd,follow_symlinks=False)
                if (current.st_dev,current.st_ino)==(created.st_dev,created.st_ino):os.unlink(temp,dir_fd=fd);os.fsync(fd)
            except FileNotFoundError:pass
        os.close(fd)
