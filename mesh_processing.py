import io
import struct
import os
import json
import time

class v200Vertex:
    def __init__(self):
        self.px = 0.0
        self.py = 0.0
        self.pz = 0.0
        self.nx = 0.0
        self.ny = 0.0
        self.nz = 0.0
        self.tu = 0.0
        self.tv = 0.0
        self.tw = 0.0
        self.tx = 0
        self.ty = 0
        self.tz = 0
        self.ts = 0
        self.r = 255
        self.g = 255
        self.b = 255
        self.a = 255

class v200Face:
    def __init__(self):
        self.a = 0
        self.b = 0
        self.c = 0

def read_vertices(stream, verts, count, szvertex):
    for i in range(count):
        verts[i].px, verts[i].py, verts[i].pz = struct.unpack('fff', stream.read(12))
        verts[i].nx, verts[i].ny, verts[i].nz = struct.unpack('fff', stream.read(12))
        verts[i].tu = struct.unpack('f', stream.read(4))[0]
        verts[i].tv = 1.0 - struct.unpack('f', stream.read(4))[0]
        verts[i].tw = 0.0
        verts[i].tx, verts[i].ty, verts[i].tz, verts[i].ts = struct.unpack('bbbb', stream.read(4))
        if szvertex == 40:
            verts[i].r, verts[i].g, verts[i].b, verts[i].a = struct.unpack('BBBB', stream.read(4))
        else:
            verts[i].r = 255
            verts[i].g = 255
            verts[i].b = 255
            verts[i].a = 255
    return verts

def read_faces(stream, num_faces):
    faces = [v200Face() for _ in range(num_faces)]
    for i in range(num_faces):
        faces[i].a = struct.unpack('I', stream.read(4))[0] + 1
        faces[i].b = struct.unpack('I', stream.read(4))[0] + 1
        faces[i].c = struct.unpack('I', stream.read(4))[0] + 1
    return faces

def read_lods(stream, num_lods):
    lods = [struct.unpack('I', stream.read(4))[0] for _ in range(num_lods)]
    return lods

def append_fix(builder, insert):
    builder.append(insert.replace(",", ".") + "\n")

def write_obj_file(output_path, version, verts, faces, lods, lod_type, num_faces):
    with open(output_path, 'w') as writer:
        writer.write(f"# Converted from Roblox Mesh {version} to obj\n")
        vert_data = []
        tex_data = []
        norm_data = []
        face_data = []

        for vert in verts:
            append_fix(vert_data, f"v {vert.px} {vert.py} {vert.pz}")
            append_fix(norm_data, f"vn {vert.nx} {vert.ny} {vert.nz}")
            append_fix(tex_data, f"vt {vert.tu} {vert.tv} 0")

        if lods and lod_type is not None:
            face_limit = num_faces if lod_type == 0 else lods[1]
        else:
            face_limit = num_faces

        for i in range(face_limit):
            face = faces[i]
            append_fix(face_data, f"f {face.a}/{face.a}/{face.a} {face.b}/{face.b}/{face.b} {face.c}/{face.c}/{face.c}")

        writer.writelines(vert_data)
        writer.writelines(norm_data)
        writer.writelines(tex_data)
        writer.writelines(face_data)

def version_5(data, output_path):
    version_info = data[:12].decode('utf-8')
    num_only_ver = version_info[8:]

    stream = io.BytesIO(data)
    stream.read(13)
    sizeof_MeshHeader = struct.unpack('H', stream.read(2))[0]
    if sizeof_MeshHeader != 32:
        print(f"[{num_only_ver}] Invalid mesh header size: {sizeof_MeshHeader}")
        return None
    lod_type = struct.unpack('H', stream.read(2))[0]
    num_verts = struct.unpack('I', stream.read(4))[0]
    num_faces = struct.unpack('I', stream.read(4))[0]
    num_lods = struct.unpack('H', stream.read(2))[0]
    num_bones = struct.unpack('H', stream.read(2))[0]
    sizeof_boneNamesBuffer = struct.unpack('I', stream.read(4))[0]
    num_subsets = struct.unpack('H', stream.read(2))[0]
    num_high_quality_lods = struct.unpack('B', stream.read(1))[0]
    stream.read(1)
    stream.read(4)
    stream.read(4)

    verts = [v200Vertex() for _ in range(num_verts)]
    verts = read_vertices(stream, verts, num_verts, 40)
    
    if num_bones > 0:
        stream.read(int(num_verts*8))
    
    faces = read_faces(stream, num_faces)
    lods = read_lods(stream, num_lods)
    write_obj_file(output_path, num_only_ver, verts, faces, lods, lod_type, num_faces)

def version_4(data, output_path):
    version_info = data[:12].decode('utf-8')
    num_only_ver = version_info[8:]

    stream = io.BytesIO(data)
    stream.read(13)
    sizeof_MeshHeader = struct.unpack('H', stream.read(2))[0]
    if sizeof_MeshHeader != 24:
        print(f"Wrong header size: {sizeof_MeshHeader}")
        return None
    lod_type = struct.unpack('H', stream.read(2))[0]
    num_verts = struct.unpack('I', stream.read(4))[0]
    num_faces = struct.unpack('I', stream.read(4))[0]
    num_lods = struct.unpack('H', stream.read(2))[0]
    num_bones = struct.unpack('H', stream.read(2))[0]
    sizeof_boneNamesBuffer = struct.unpack('I', stream.read(4))[0]
    num_subsets = struct.unpack('H', stream.read(2))[0]
    num_high_quality_lods = struct.unpack('B', stream.read(1))[0]
    stream.read(1)

    verts = [v200Vertex() for _ in range(num_verts)]
    verts = read_vertices(stream, verts, num_verts, 40)

    if num_bones > 0:
        stream.read(int(num_verts*8))

    faces = read_faces(stream, num_faces)
    lods = read_lods(stream, num_lods)
    write_obj_file(output_path, num_only_ver, verts, faces, lods, lod_type, num_faces)

def version_3(data, output_path):
    version_info = data[:12].decode('utf-8')
    num_only_ver = version_info[8:]
    stream = io.BytesIO(data)
    stream.read(13)
    szmeshHeader = struct.unpack('H', stream.read(2))[0]
    szvertex = struct.unpack('B', stream.read(1))[0]
    szface = struct.unpack('B', stream.read(1))[0]
    szLOD = struct.unpack('H', stream.read(2))[0]
    cLODs = struct.unpack('H', stream.read(2))[0]
    cverts = struct.unpack('I', stream.read(4))[0]
    cfaces = struct.unpack('I', stream.read(4))[0]

    verts = [v200Vertex() for _ in range(cverts)]
    verts = read_vertices(stream, verts, cverts, szvertex)
    faces = read_faces(stream, cfaces)
    lods = read_lods(stream, cLODs)
    write_obj_file(output_path, num_only_ver, verts, faces, lods, lod_type=0, num_faces=cfaces)

def version_2(data, output_path):
    version_info = data[:12].decode('utf-8')
    num_only_ver = version_info[8:]
    stream = io.BytesIO(data)
    stream.read(13)
    szmeshHeader = struct.unpack('H', stream.read(2))[0]
    szvertex = struct.unpack('B', stream.read(1))[0]
    szface = struct.unpack('B', stream.read(1))[0]
    cverts = struct.unpack('I', stream.read(4))[0]
    cfaces = struct.unpack('I', stream.read(4))[0]
    verts = [v200Vertex() for _ in range(cverts)]
    verts = read_vertices(stream, verts, cverts, szvertex)
    faces = read_faces(stream, cfaces)
    write_obj_file(output_path, num_only_ver, verts, faces, lods=None, lod_type=None, num_faces=cfaces)

def version_1(data, output_path):
    version_info = data[:12].decode('utf-8')
    num_only_ver = version_info[8:]
    data = data.decode('utf-8')
    lines = data.split('\n')
    num_faces = int(lines[1])
    content = json.loads("[" + lines[2].replace("][", "],[") + "]")
    true_faces = len(content) // 3

    with open(output_path, 'w') as writer:
        writer.write(f"# Converted from Roblox Mesh {num_only_ver} to obj\n")
        vert_data = []
        tex_data = []
        norm_data = []
        face_data = []

        for i in range(true_faces):
            vert = content[i * 3]
            norm = content[i * 3 + 1]
            uv = content[i * 3 + 2]
            append_fix(vert_data, f"v {vert[0]} {vert[1]} {vert[2]}")
            append_fix(norm_data, f"vn {norm[0]} {norm[1]} {norm[2]}")
            append_fix(tex_data, f"vt {uv[0]} {1.0 - uv[1]} {uv[2]}")

        for i in range((true_faces - 1) // 3):
            pos = (i * 3 + 1)
            append_fix(face_data, f"f {pos}/{pos}/{pos} {pos + 1}/{pos + 1}/{pos + 1} {pos + 2}/{pos + 2}/{pos + 2}")

        writer.writelines(vert_data)
        writer.writelines(norm_data)
        writer.writelines(tex_data)
        writer.writelines(face_data)

def convert(data, output_path):
    version_index = data.find(b'version ')
    if version_index != -1:
        data = data[version_index:]

    version_info = data[:12].decode('utf-8')
    num_only_ver = version_info[8:]
    if num_only_ver == '5.00':
        print(f"[{time.strftime('%H:%M:%S')}] Processing v5 mesh")
        version_5(data, output_path)
    elif num_only_ver == '4.00' or num_only_ver == '4.01':
        print(f"[{time.strftime('%H:%M:%S')}] Processing v4 mesh")
        version_4(data, output_path)
    elif num_only_ver == '3.00' or num_only_ver == '3.01':
        print(f"[{time.strftime('%H:%M:%S')}] Processing v3 mesh")
        version_3(data, output_path)
    elif num_only_ver == '2.00':
        print(f"[{time.strftime('%H:%M:%S')}] Processing v2 mesh")
        version_2(data, output_path)
    elif num_only_ver == '1.00' or num_only_ver == '1.01':
        print(f"[{time.strftime('%H:%M:%S')}] Processing v1 mesh")
        version_1(data, output_path)
    else:
        print(f"[{time.strftime('%H:%M:%S')}] Unsupported version: {num_only_ver}")