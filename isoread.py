#!/usr/bin/env python3
"""Minimal ISO9660 lister/extractor for raw MODE2/2352 PlayStation BIN dumps."""
import struct
import sys

SEC = 2352


class Iso:
    def __init__(self, path):
        self.f = open(path, 'rb')

    def sector(self, lba):
        self.f.seek(SEC * lba)
        raw = self.f.read(SEC)
        if len(raw) < SEC:
            return b'\0' * 2048
        return raw[24:24 + 2048]

    def list_root(self):
        pvd = self.sector(16)
        if pvd[1:6] != b'CD001':
            raise SystemExit('not an ISO9660 image')
        print('volume id:', pvd[40:72].decode('latin1').strip())
        root = pvd[156:190]
        lba = struct.unpack('<I', root[2:6])[0]
        size = struct.unpack('<I', root[10:14])[0]
        self.walk(lba, size, '')

    def walk(self, lba, byte_size, prefix, depth=0):
        if depth > 8 or byte_size <= 0 or byte_size > 1 << 20:
            return
        sectors = (byte_size + 2047) // 2048
        buf = b''.join(self.sector(lba + i) for i in range(sectors))[:byte_size]
        i = 0
        while i < len(buf):
            reclen = buf[i]
            if reclen == 0:
                i = (i // 2048 + 1) * 2048
                continue
            ext_lba = struct.unpack('<I', buf[i + 2:i + 6])[0] & 0x00FFFFFF
            ext_len = struct.unpack('<I', buf[i + 10:i + 14])[0]
            flags = buf[i + 25]
            nlen = buf[i + 32]
            name = buf[i + 33:i + 33 + nlen].decode('latin1')
            i += reclen
            if name in ('\x00', '\x01'):
                continue
            if flags & 2:
                print(f'{prefix}{name}/')
                self.walk(ext_lba, ext_len, prefix + name + '/', depth + 1)
            else:
                print(f'{prefix}{name}\tlba={ext_lba}\tsize={ext_len}')

    def extract(self, lba, size, dest):
        with open(dest, 'wb') as out:
            remaining = size
            cur = lba
            while remaining > 0:
                chunk = min(remaining, 32)
                for i in range(chunk):
                    out.write(self.sector(cur + i))
                remaining -= chunk
                cur += chunk


if __name__ == '__main__':
    iso = Iso(sys.argv[1])
    iso.list_root()