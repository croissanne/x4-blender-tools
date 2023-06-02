# exports each selected object into its own file
# Z and Y are regularly swapped around!!!
# please make sure all rotations are in quaternions except for animations
# does not support delta_location or delta_rotation
# parts that are animated need their origin to be at their center => at  0, 0, 0
# limitations regarding animations:
# - need to pick between having an offset (for either rotation or location) or an animation
# - if mixed will behave weirdly, especially with a parent set
# - rotation animations which have parents which rotate as well don't work


import bpy
import copy
import logging
import math
import mathutils
import os
import xml.etree.cElementTree as ET

from struct import pack

# export to blend file location
BASEDIR = os.path.dirname(bpy.data.filepath)
COLLECTION_CONNECTIONS = "connections"
COLLECTION_PARTS = "parts"
EXTENSION_NAME = "x3_ships"
PROJECT_NAME = bpy.path.display_name_from_filepath(bpy.data.filepath)
SHIP_CLASS = bpy.context.scene.classAttr

ROUND_POSITION = 6
ROUND_ROTATION = 7
ROUND_DIMENSION = 6

FPS = bpy.context.scene.render.fps

INDEX_X = 0
INDEX_Y = 1
INDEX_Z = 2

LOG_FILE = os.path.join(BASEDIR, "log.txt")
logging.basicConfig(filename=LOG_FILE,
                    filemode='w',
                    format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S',
                    level=logging.DEBUG)


if not BASEDIR:
    raise Exception("Blend file is not saved")

def add_source(xml_root):
    ship_size = ""
    match SHIP_CLASS:
        case "ship_s":
            ship_size = "size_s"
    ET.SubElement(xml_root, "source",
        geometry=f"extensions\\{EXTENSION_NAME}\\assets\\units\\{ship_size}\\{PROJECT_NAME}_data")

def gather_tags(obj):
    tags = []
    if obj.type == "MESH":
        geometry_tags =  [ct for ct in dir(obj.GeometryTags) if not ct.startswith('__')]
        for tag in geometry_tags:
            if getattr(obj.GeometryTags, tag) == True:
                tags.append(tag)
    if obj.type == "EMPTY":
        connection_tags = [ct for ct in dir(obj.ConnectionTags) if not ct.startswith('__')]
        for tag in connection_tags:
            if getattr(obj.ConnectionTags, tag) == True:
                tags.append(tag)
        if "Symmetry" in obj:
            symmetry_tags =  [st for st in dir(obj.Symmetry) if st.startswith('symmetry')]
            for tag in symmetry_tags:
                if getattr(obj.Symmetry, tag) == True:
                    tags.append(tag)

    return tags

def has_animation(obj):
    return obj.animation_data and len(obj.animation_data.nla_tracks)

# sort actions by end and start frame
def sorted_animations(obj):
    anims = []
    if not has_animation(obj):
        return anims

    nla_tracks = obj.animation_data.nla_tracks
    for nt in obj.animation_data.nla_tracks:
        if nt.mute:
            logging.info("Animation %s_%s is muted", obj.name, nt.name)
            continue
        for strip in nt.strips:
            assert strip.action_frame_start % 1 == 0
            assert strip.action_frame_end % 1 == 0
            anims.append({
                "name": f"{nt.name}_{strip.name}",
                "start": int(strip.action_frame_start),
                "end": int(strip.action_frame_end),
                "action": strip.action,
            })
            if strip.action_frame_end <= strip.action_frame_start:
                raise Exception(f"Each animation should be at least 1 frame: {nt.name}_{strip.name}")
    anims.sort(key=lambda a: (a["end"], a["start"]))
    return anims

# ANIMATIONS
# heavily inspired by https://github.com/tomchk/X4_gen_regions, without the need to generate the xml first
INTERPOLATION_DICT = {
    "UNKNOWN"          : b'\x00\x00\x00\x00',
    "CONSTANT"         : b'\x01\x00\x00\x00',
    "LINEAR"           : b'\x02\x00\x00\x00',
    "QUADRATIC"        : b'\x03\x00\x00\x00',
    "CUBIC"            : b'\x04\x00\x00\x00',
    "BEZIER"           : b'\x05\x00\x00\x00',
    "BEZIER_LINEARTIME": b'\x06\x00\x00\x00',
    "TCB"              : b'\x07\x00\x00\x00',
}

def kf_filt(kf, start, end):
    return kf[1].co[0] >= start and kf[1].co[0] <= end

# Collects the duration, name, subname, and keyframes of each animation
# Rotation needs to be in euler
def gen_anims(obj):
    sorted_anims = sorted_animations(obj)
    anims = []

    for anim in sorted_anims:
        action = anim["action"]
        loc_fcs = list(filter(lambda f: f[1].data_path == "location", action.fcurves.items()))
        has_loc = len(loc_fcs) > 0
        if has_loc:
            assert len(loc_fcs) == 3

        rot_fcs = list(filter(lambda f: f[1].data_path == "rotation_euler", action.fcurves.items()))
        has_rot = len(rot_fcs) > 0
        if has_rot:
            assert len(rot_fcs) == 3

        if has_loc:
            loc_kfs_x = loc_fcs[INDEX_X][1].keyframe_points
            loc_kfs_y = loc_fcs[INDEX_Y][1].keyframe_points
            loc_kfs_z = loc_fcs[INDEX_Z][1].keyframe_points
    
        if has_rot:
            rot_kfs_x = rot_fcs[INDEX_X][1].keyframe_points
            rot_kfs_y = rot_fcs[INDEX_Y][1].keyframe_points
            rot_kfs_z = rot_fcs[INDEX_Z][1].keyframe_points

        start = anim["start"]
        end = anim["end"]
        duration = end - start
        # Here we swap y for z!
        a = {
            "name": obj.name,
            "subname": anim["name"],
            "duration": duration,
            "start": start,
            "end": end,
        }
        if has_loc:
            a["loc_kfs_x"] = list(filter(lambda kf: kf_filt(kf, start, end), loc_kfs_x.items()))
            a["loc_kfs_y"] = list(filter(lambda kf: kf_filt(kf, start, end), loc_kfs_z.items()))
            a["loc_kfs_z"] = list(filter(lambda kf: kf_filt(kf, start, end), loc_kfs_y.items()))
        if has_rot:
            a["rot_kfs_x"] = list(filter(lambda kf: kf_filt(kf, start, end), rot_kfs_x.items()))
            a["rot_kfs_y"] = list(filter(lambda kf: kf_filt(kf, start, end), rot_kfs_z.items()))
            a["rot_kfs_z"] = list(filter(lambda kf: kf_filt(kf, start, end), rot_kfs_y.items()))
        anims.append(a)
    return anims


# Relies on custom properties being set on the action
def add_animations(xml_conn, obj):
    if not has_animation(obj):
        return
    xml_anims = ET.SubElement(xml_conn, "animations")
    for a in sorted_animations(obj):
        ET.SubElement(xml_anims, "animation", name=a["name"], start=str(a["start"]), end=str(a["end"]))


# todo correct?
def quat_right_to_left_hand(quat):
    return mathutils.Quaternion((quat.w, -quat.x, -quat.y, -quat.z))


def add_parts(xml_conn, obj):
    if obj.type != "MESH":
        return

    xml_parts = ET.SubElement(xml_conn, "parts")
    xml_part = ET.SubElement(xml_parts, "part", name=obj.name)

    xml_lods =  ET.SubElement(xml_part, "lods")
    xml_lod_0 = ET.SubElement(xml_lods, "lod", index="0")
    xml_lod_0_materials = ET.SubElement(xml_lod_0, "materials")
    for i, mat in enumerate(obj.data.materials):
        xml_lod_0_materials_0 = ET.SubElement(xml_lod_0_materials, "material", id=str(i + 1), ref=mat.name)

    xml_size = ET.SubElement(xml_part, "size")
    xml_size_max = ET.SubElement(xml_size, "max", x=str(round(obj.dimensions.x, ROUND_DIMENSION)),
        y=str(round(obj.dimensions.z, ROUND_DIMENSION)), z=str(round(obj.dimensions.y, ROUND_DIMENSION)))

    xml_size_center = ET.SubElement(xml_size, "center", x="0", y="0", z="0")


def add_offset(xml_conn, obj):
    xml_offset = ET.SubElement(xml_conn, "offset")
    
    anims = gen_anims(obj)
    
    # Only add offset element if it's not controlled by animations
    anim_loc = False
    anim_rot = False
    if len(anims):
        if "loc_kfs_x" in anims[0]:
            anim_loc = True
        if "rot_kfs_x" in anims[0]:
            anim_rot = True

    if not anim_loc:
        loc_x, loc_y, loc_z = obj.location.xzy
        xml_pos = ET.SubElement(xml_offset, "position",
            x=str(round(loc_x, ROUND_POSITION)), y=str(round(loc_y, ROUND_POSITION)), z=str(round(loc_z, ROUND_POSITION)))

    if not anim_rot:
      rot = quat_right_to_left_hand(obj.rotation_quaternion)
      xml_rot = ET.SubElement(xml_offset, "quaternion",
           qx=str(round(rot.x, ROUND_ROTATION)), qy=str(round(rot.z, ROUND_ROTATION)),
           qz=str(round(rot.y, ROUND_ROTATION)), qw=str(round(rot.w, ROUND_ROTATION)))


def gen_connections(xml_root):
    xml_connections = ET.SubElement(xml_root, "connections")

    ET.SubElement(xml_connections, "connection", name="space", tags="ship_s ship")
    ET.SubElement(xml_connections, "connection", name="position", tags="position", value="1")

    for collection in bpy.data.collections:
        if collection.name.casefold() not in [COLLECTION_CONNECTIONS, COLLECTION_PARTS]:
            continue

        for obj in collection.objects:
            # skip objects which shouldn't be rendered
            if obj.hide_render:
                continue
            
            # if an object has scale set, error out as egosoft doesn't support this in component xml
            if obj.scale != mathutils.Vector((1.0, 1.0, 1.0)):
                raise Exception("Please apply all scaling before running")

            tags = gather_tags(obj)
            parent = obj.parent
            xml_conn_attr = {
                "name": obj.name,
                "tags": " ".join(tags),
            }
            if parent:
                xml_conn_attr["parent"] = parent.name
            if obj.value != 0:
                xml_conn_attr["value"] = str(obj.value)

            xml_conn = ET.SubElement(xml_connections, "connection", xml_conn_attr)
            add_parts(xml_conn, obj)
            add_animations(xml_conn, obj)
            add_offset(xml_conn, obj)


xml_components = ET.Element("components")
xml_component = ET.SubElement(xml_components, "component", {
    "name": PROJECT_NAME,
    "class": SHIP_CLASS,
})

add_source(xml_component)
gen_connections(xml_component)
tree = ET.ElementTree(xml_components)
ET.indent(tree)
tree.write(os.path.join(BASEDIR, "connections.xml"), encoding="utf-8", xml_declaration=True)
tree.write(os.path.join(BASEDIR, PROJECT_NAME + ".xml"), encoding="utf-8", xml_declaration=True)


# writes out the description of each animation
def write_ani_descr(ani_file, anims):
    for a in anims:
        duration = float(a["duration"] / FPS)
        loc_kfs = 0
        if "loc_kfs_x" in a:
            loc_kfs = int(len(a["loc_kfs_x"]))
        rot_kfs = 0            
        if "rot_kfs_x" in a:
            rot_kfs = int(len(a["rot_kfs_x"]))

        ani_file.write(
            pack("64s64sIIIIIfII",
                a["name"].encode(),
                a["subname"].encode(),
                loc_kfs, # loc_kfs
                rot_kfs, # rot_kfs
                0, # scale_kfs
                0, # prescale
                0, # postscale
                duration,
                0, # no idea about the last 2
                0))


# writes out the keyframes for each animation
def write_ani_keyframes(ani_file, anims):
    for a in anims:
        loc_kfs_x = []
        if "loc_kfs_x" in a:
            loc_kfs_x = a["loc_kfs_x"]
            loc_kfs_y = a["loc_kfs_y"]
            loc_kfs_z = a["loc_kfs_z"]
            assert len(loc_kfs_x) == len(loc_kfs_y) == len(loc_kfs_z)

        rot_kfs_x = []
        if "rot_kfs_x" in a:
            rot_kfs_x = a["rot_kfs_x"]
            rot_kfs_y = a["rot_kfs_y"]
            rot_kfs_z = a["rot_kfs_z"]
            assert len(rot_kfs_x) == len(rot_kfs_y) == len(rot_kfs_z)

        for kfi in range(len(loc_kfs_x)):
            kf_x = loc_kfs_x[kfi][1]
            kf_y = loc_kfs_y[kfi][1]
            kf_z = loc_kfs_z[kfi][1]

            # x, y and z have to describe the same kf
            assert kf_x.co[0] == kf_y.co[0] == kf_z.co[0]
            # TODO they can differ?!
            assert kf_x.interpolation == kf_y.interpolation == kf_z.interpolation
            
            # location in time relative to the first frame of the animation
            time_loc = float(kf_x.co[0] - a["start"]) / FPS
            assert time_loc <= a["duration"] / FPS

            ani_file.write(pack('fff4s4s4sffffffffffffffffffIffffffI',
                float(kf_x.co[1]),
                float(kf_y.co[1]),
                float(kf_z.co[1]),
                INTERPOLATION_DICT[kf_x.interpolation],
                INTERPOLATION_DICT[kf_y.interpolation],
                INTERPOLATION_DICT[kf_z.interpolation],
                time_loc,
                float(kf_x.handle_right[INDEX_X]),
                float(kf_x.handle_right[INDEX_Y]),
                float(kf_x.handle_left[INDEX_X]),
                float(kf_x.handle_left[INDEX_Y]),
                float(kf_y.handle_right[INDEX_X]),
                float(kf_y.handle_right[INDEX_Y]),
                float(kf_y.handle_left[INDEX_X]),
                float(kf_y.handle_left[INDEX_Y]),
                float(kf_z.handle_right[INDEX_X]),
                float(kf_z.handle_right[INDEX_Y]),
                float(kf_z.handle_left[INDEX_X]),
                float(kf_z.handle_left[INDEX_Y]),
                0,
                0,
                0,
                0,
                0,
                int(0),
                0,
                0,
                0,
                0,
                0,
                0,
                int(0)))

        for kfi in range(len(rot_kfs_x)):
            kf_x = rot_kfs_x[kfi][1]
            kf_y = rot_kfs_y[kfi][1]
            kf_z = rot_kfs_z[kfi][1]
            
            # x, y and z describe the same kf
            assert kf_x.co[0] == kf_y.co[0] == kf_z.co[0]
            assert kf_x.interpolation == kf_y.interpolation == kf_z.interpolation

            # location in time relative to the first frame of the animation
            time_loc = float(kf_x.co[0] - a["start"]) / FPS
            assert time_loc <= a["duration"] / FPS
           
            ani_file.write(pack('fff4s4s4sffffffffffffffffffIffffffI',
                float(kf_x.co[1]),
                float(kf_y.co[1]),
                float(kf_z.co[1]),
                INTERPOLATION_DICT[kf_x.interpolation],
                INTERPOLATION_DICT[kf_y.interpolation],
                INTERPOLATION_DICT[kf_z.interpolation],
                time_loc,
                float(kf_x.handle_right[INDEX_X]),
                float(kf_x.handle_right[INDEX_Y]),
                float(kf_x.handle_left[INDEX_X]),
                float(kf_x.handle_left[INDEX_Y]),
                float(kf_y.handle_right[INDEX_X]),
                float(kf_y.handle_right[INDEX_Y]),
                float(kf_y.handle_left[INDEX_X]),
                float(kf_y.handle_left[INDEX_Y]),
                float(kf_z.handle_right[INDEX_X]),
                float(kf_z.handle_right[INDEX_Y]),
                float(kf_z.handle_left[INDEX_X]),
                float(kf_z.handle_left[INDEX_Y]),
                0,
                0,
                0,
                0,
                0,
                int(0),
                0,
                0,
                0,
                0,
                0,
                0,
                int(0)))

def gen_ani(ani_file):
    anims = []
    for collection in bpy.data.collections:
        if collection.name.casefold() not in [COLLECTION_PARTS]:
            continue
        for obj in collection.objects:
            # skip objects which shouldn't be rendered
            if obj.hide_render:
                continue
            if not has_animation(obj):
                continue
            

            anims += gen_anims(obj)
    ani_file.write(pack('IIII', int(len(anims)), int(16 + len(anims) * 160), int(1), int(0))) # Write 16-byte header:'number of animations','KeyOffsetBytes'(16 + len(anims)*160),'Version','Padding'
    write_ani_descr(ani_file, anims)
    write_ani_keyframes(ani_file, anims)

ANI_FILE = os.path.join(BASEDIR, PROJECT_NAME.upper() + "_DATA.ani")
with open(ANI_FILE, 'wb') as af:
    gen_ani(af)
logging.getLogger().handlers[0].flush()