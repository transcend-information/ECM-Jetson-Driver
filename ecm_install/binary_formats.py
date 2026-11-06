"""Bounded, data-only readers for original KO/DTBO identity. No external tools."""
import struct
from .contracts import require


def build_id(note):
    offset = 0
    found = []
    while offset < len(note):
        require(offset + 12 <= len(note), 'Truncated ELF note')
        namesz, descsz, kind = struct.unpack_from('<III', note, offset)
        offset += 12
        end = offset + ((namesz + 3) & ~3)
        finish = end + ((descsz + 3) & ~3)
        require(finish <= len(note), 'Truncated ELF note data')
        if note[offset:offset+namesz] == b'GNU\0' and kind == 3:
            require(0 < descsz <= 64, 'Invalid build ID size')
            found.append(note[end:end+descsz].hex())
        offset = finish
    require(len(found) <= 1, 'Ambiguous GNU build ID')
    return found[0] if found else None


def elf_build_id(body):
    require(len(body) >= 64 and body[:6] == b'\x7fELF\x02\x01', 'Unsupported ELF encoding')
    shoff = struct.unpack_from('<Q', body, 40)[0]
    size, count, index = struct.unpack_from('<HHH', body, 58)
    require(size == 64 and 0 < count < 8192 and index < count and shoff + count*size <= len(body), 'Invalid ELF sections')
    sections = [struct.unpack_from('<IIQQQQIIQQ', body, shoff+i*size) for i in range(count)]
    def data(s):
        require(s[4]+s[5] <= len(body), 'ELF section exceeds file')
        return body[s[4]:s[4]+s[5]]
    names = data(sections[index])
    notes = []
    for section in sections:
        require(section[0] < len(names), 'Invalid ELF name')
        if names[section[0]:].split(b'\0', 1)[0] == b'.note.gnu.build-id':
            notes.append(build_id(data(section)))
    require(len(notes) <= 1, 'Ambiguous ELF identity section')
    return notes[0] if notes else None


def fdt_nodes(body):
    require(40 <= len(body) <= 4*1024*1024, 'DTBO size outside parser bound')
    magic, total, off, strings_off, _, version, _, _, strings_len, tree_len = struct.unpack_from('>10I', body)
    require(magic == 0xd00dfeed and total == len(body) and version >= 17 and
            off + tree_len <= total and strings_off + strings_len <= total, 'Invalid DTBO header')
    names = body[strings_off:strings_off+strings_len]
    tree = body[off:off+tree_len]; cursor = 0; stack = []; nodes = {}; ended = False
    while cursor+4 <= len(tree):
        kind = struct.unpack_from('>I', tree, cursor)[0]; cursor += 4
        if kind == 1:
            end = tree.find(b'\0', cursor)
            require(end >= cursor, 'Truncated DT node')
            name = tree[cursor:end].decode('ascii'); cursor = (end+4)&~3
            require('/' not in name and name not in ('.','..') and len(stack) < 64, 'Invalid DT node')
            stack.append(name); path = '/'+'/'.join(stack[1:])
            require(path not in nodes and len(nodes) < 4096, 'Duplicate/excess DT node')
            nodes[path] = {}
        elif kind == 2:
            require(bool(stack), 'Unbalanced DT node'); stack.pop()
        elif kind == 3:
            require(stack and cursor+8 <= len(tree), 'Invalid DT property')
            length, index = struct.unpack_from('>II', tree, cursor); cursor += 8
            require(index < len(names) and cursor+length <= len(tree), 'Truncated DT property')
            end = names.find(b'\0', index); require(end >= index, 'Truncated DT property name')
            name = names[index:end].decode('ascii')
            require('/' not in name and name not in ('.','..'), 'Invalid DT property name')
            props = nodes['/'+'/'.join(stack[1:])]
            require(name not in props, 'Duplicate DT property'); props[name] = tree[cursor:cursor+length]
            cursor = (cursor+length+3)&~3
        elif kind == 4:
            pass
        elif kind == 9:
            require(not stack, 'Unclosed DT node'); ended = True; break
        else:
            raise ValueError('Unsupported FDT token')
    require(ended, 'Missing FDT end')
    return nodes
