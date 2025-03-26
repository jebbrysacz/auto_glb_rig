import bpy
import mathutils
import sys

def auto_rig_glb(filepath):
    """Automatically import a GLB file and rig it as humanoid or quadruped with auto weights.
    
    Args:
        filepath (str): Filesystem path to the .glb file to import.
    Returns:
        bpy.types.Object: The created Armature object, or None if failed.
    """
    # Ensure nothing is selected
    bpy.ops.object.select_all(action='DESELECT')
    # Import the GLB file (glTF format). This will create one or more objects in the scene.
    try:
        bpy.ops.import_scene.gltf(filepath=filepath)
    except Exception as e:
        print(f"Failed to import {filepath}: {e}")
        return None
    
    # After import, assume the main character mesh is the active object (glTF importer usually selects the new objects).
    # We will try to pick the first mesh object in the imported hierarchy.
    imported_objs = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
    if not imported_objs:
        print("No mesh found in imported file.")
        return None
    # For simplicity, take the first mesh object as the character
    mesh_obj = imported_objs[0]
    bpy.context.view_layer.objects.active = mesh_obj
    
    # Apply rotation and scale transforms on the mesh to simplify calculations (so mesh data is in world scale).
    mesh_obj.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    mesh_obj.select_set(False)
    
    # Get basic dimensions of the mesh bounding box
    dimensions = mesh_obj.dimensions
    height = dimensions.z
    length = dimensions.y
    width  = dimensions.x
    
    # Determine if humanoid or quadruped based on bounding box and foot clustering.
    # We will attempt a simple ground contact analysis:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh_eval = mesh_obj.evaluated_get(depsgraph)  # evaluated mesh to get updated data if any modifiers
    mesh_data = mesh_eval.data
    verts = [mesh_obj.matrix_world @ v.co for v in mesh_data.vertices]  # world-coordinate verts
    
    # Find min Z (ground level) and collect vertices near that plane
    if verts:
        min_z = min(v.z for v in verts)
    else:
        min_z = 0.0
    # Tolerance for "ground contact" (we take vertices within a small epsilon of the lowest point)
    eps = 0.05 * height  # e.g., 5% of height
    foot_verts = [v for v in verts if v.z <= min_z + eps]
    # Split foot verts into left vs right by X coordinate
    left_foot_verts  = [v for v in foot_verts if v.x < 0]
    right_foot_verts = [v for v in foot_verts if v.x > 0]
    # Count distinct foot groups
    num_feet = 0
    if left_foot_verts:
        num_feet += 1 if not right_foot_verts else 2  # if only one side has verts, count 1, if both sides, at least 2
    elif right_foot_verts:
        num_feet += 1
    # Further refine: if both left and right exist, check if each side likely has two distinct paws (quadruped)
    is_quadruped = False
    if left_foot_verts and right_foot_verts:
        # Compute Y-range on each side
        left_y_range = (max(v.y for v in left_foot_verts) - min(v.y for v in left_foot_verts)) if left_foot_verts else 0
        right_y_range = (max(v.y for v in right_foot_verts) - min(v.y for v in right_foot_verts)) if right_foot_verts else 0
        # If both sides have a large foot spread in Y (relative to total length), assume quadruped
        total_y_range = max(v.y for v in verts) - min(v.y for v in verts) if verts else 0
        if left_y_range > 0.3 * total_y_range and right_y_range > 0.3 * total_y_range:
            is_quadruped = True
    elif num_feet >= 3:
        # If somehow 3 or 4 separate foot clusters found (unlikely without both sides), treat as quadruped
        is_quadruped = True
    
    # We will use the above logic to decide:
    char_type = "Quadruped" if is_quadruped else "Humanoid"
    print(f"Detected character type: {char_type}")
    
    # Create a new armature object for the rig
    rig_data = bpy.data.armatures.new(name="AutoRigArmature")
    rig_obj = bpy.data.objects.new(name="AutoRigArmature", object_data=rig_data)
    bpy.context.collection.objects.link(rig_obj)
    
    # Enter edit mode on the armature to add bones
    bpy.context.view_layer.objects.active = rig_obj
    bpy.ops.object.mode_set(mode='EDIT')
    arm = rig_data  # shorthand for armature data (EditBones accessible here)
    
    # Utility: create a bone with given head and tail, parent, and name
    def create_bone(name, head, tail, parent=None):
        bone = arm.edit_bones.new(name)
        bone.head = head
        bone.tail = tail
        if parent:
            bone.parent = parent
            # Optionally connect bone to parent if head == parent.tail
            # (We won't connect by default to allow independent positioning)
            bone.use_connect = False
        return bone
    
    # Calculate some key coordinates for bone placement:
    # Center of mesh at ground (pelvis center) and top of mesh (head top)
    world_mat = mesh_obj.matrix_world
    # Using bounding box extremes for simplicity:
    min_bb = mathutils.Vector(( -width/2, -length/2, min_z ))  # assuming mesh roughly centered at origin
    max_bb = mathutils.Vector((  width/2,  length/2,  height+min_z ))
    # Transform these by mesh_obj (if not at origin)
    min_bb_world = world_mat @ min_bb
    max_bb_world = world_mat @ max_bb
    # Pelvis: roughly mid of bottom in XY, slightly above min Z
    pelvis_x = (min_bb_world.x + max_bb_world.x) / 2
    pelvis_y = (min_bb_world.y + max_bb_world.y) / 2
    pelvis_z = min_z + 0.7 * height  # put pelvis a bit above ground (e.g. 10% of height)
    pelvis_pos = mathutils.Vector((pelvis_x, pelvis_y, pelvis_z))
    # Head position: directly above pelvis at max height
    head_pos = mathutils.Vector((pelvis_x, pelvis_y, max_bb_world.z))
    
    if not is_quadruped:
        # Humanoid rig bones:
        # Pelvis bone (root of legs and spine)
        spine_base = create_bone("Spine", pelvis_pos, pelvis_pos + mathutils.Vector((0, 0, 0.3 * height)))
        # Spine bone upward (spine_base to chest)
        spine_top = create_bone("Spine.001", spine_base.tail, spine_base.tail + mathutils.Vector((0, 0, 0.3 * height)), parent=spine_base)
        # Neck/shoulder bone from chest to base of neck
        neck = create_bone("Spine.002", spine_top.tail, spine_top.tail + mathutils.Vector((0, 0, 0.2 * height)), parent=spine_top)
        # Head bone from neck to head top
        head_bone = create_bone("Head", neck.tail, head_pos, parent=neck)
        # Calculate shoulder positions: assume shoulders are around neck.tail height, and spread out on X-axis
        shoulder_z = neck.tail.z
        shoulder_y = neck.tail.y
        shoulder_offset = width * 0.5  # half the width as approximate shoulder reach
        left_shoulder_pos = mathutils.Vector((pelvis_x + shoulder_offset, shoulder_y, shoulder_z))
        right_shoulder_pos = mathutils.Vector((pelvis_x - shoulder_offset, shoulder_y, shoulder_z))
        # Arms (upper arm and forearm)
        left_arm = create_bone("upper_arm.L", left_shoulder_pos, left_shoulder_pos + mathutils.Vector((0, 0, -0.25 * height)), parent=spine_top)
        left_forearm = create_bone("lower_arm.L", left_arm.tail, left_arm.tail + mathutils.Vector((0, 0, -0.25 * height)), parent=left_arm)
        # Similarly for right arm
        right_arm = create_bone("upper_arm.R", right_shoulder_pos, right_shoulder_pos + mathutils.Vector((0, 0, -0.25 * height)), parent=spine_top)
        right_forearm = create_bone("lower_arm.R", right_arm.tail, right_arm.tail + mathutils.Vector((0, 0, -0.25 * height)), parent=right_arm)
        # Hands (end bones, just a small bone at the end of forearms)
        left_hand = create_bone("hand.L", left_forearm.tail, left_forearm.tail + mathutils.Vector((0, 0, -0.1 * height)), parent=left_forearm)
        right_hand = create_bone("hand.R", right_forearm.tail, right_forearm.tail + mathutils.Vector((0, 0, -0.1 * height)), parent=right_forearm)
        # Legs (thigh and shin)
        hip_offset = width * 0.2  # horizontal offset of hips from center
        hip_z = pelvis_pos.z
        hip_y = pelvis_pos.y
        left_hip_pos = mathutils.Vector((pelvis_x + hip_offset, hip_y, hip_z))
        right_hip_pos = mathutils.Vector((pelvis_x - hip_offset, hip_y, hip_z))
        # Create thigh bones
        left_thigh = create_bone("thigh.L", left_hip_pos, left_hip_pos + mathutils.Vector((0, 0, -0.45 * height)), parent=spine_base)
        right_thigh = create_bone("thigh.R", right_hip_pos, right_hip_pos + mathutils.Vector((0, 0, -0.45 * height)), parent=spine_base)
        # Shin bones from knee to foot
        left_shin = create_bone("shin.L", left_thigh.tail, mathutils.Vector((left_thigh.tail.x, left_thigh.tail.y, min_z + 0.05 * height)), parent=left_thigh)
        right_shin = create_bone("shin.R", right_thigh.tail, mathutils.Vector((right_thigh.tail.x, right_thigh.tail.y, min_z + 0.05 * height)), parent=right_thigh)
        # Foot bones (end at ground level slightly forward perhaps)
        left_foot = create_bone("foot.L", left_shin.tail, mathutils.Vector((left_shin.tail.x, left_shin.tail.y + 0.05 * length, min_z)), parent=left_shin)
        right_foot = create_bone("foot.R", right_shin.tail, mathutils.Vector((right_shin.tail.x, right_shin.tail.y + 0.05 * length, min_z)), parent=right_shin)
    else:
        # Quadruped rig bones:
        # Hips to shoulders spine (horizontal spine)
        hip_center = mathutils.Vector((pelvis_x, pelvis_y - 0.25 * length, pelvis_pos.z))  # a bit toward back
        shoulder_center = mathutils.Vector((pelvis_x, pelvis_y + 0.25 * length, pelvis_pos.z))
        spine = create_bone("Spine", hip_center, shoulder_center)
        # Neck from shoulder_center up to head base
        neck_base = shoulder_center  + mathutils.Vector((0, 0, 0.1 * height))
        neck_top = mathutils.Vector((pelvis_x, pelvis_y + 0.25 * length, head_pos.z * 0.8))  # just an estimate for head base
        neck_bone = create_bone("Neck", neck_base, neck_top, parent=spine)
        # Head position: directly above shoulders at max height
        head_pos = mathutils.Vector((pelvis_x, pelvis_y + 0.5, max_bb_world.z))
        head_bone = create_bone("Head", neck_top, head_pos, parent=neck_bone)
        # Legs: front (forelegs) and back (hind legs)
        # Determine approximate shoulder and hip width (half the mesh width)
        shoulder_offset = width * 0.3
        hip_offset = width * 0.3
        # Front legs (attached near shoulder_center)
        front_shoulder_L = shoulder_center + mathutils.Vector(( shoulder_offset, 0, 0))
        front_shoulder_R = shoulder_center + mathutils.Vector(( -shoulder_offset, 0, 0))
        # Place front upper leg bones (shoulder to elbow)
        upper_front_L = create_bone("upper_arm.L", front_shoulder_L, front_shoulder_L + mathutils.Vector((0, 0, -0.5 * height)), parent=spine)
        upper_front_R = create_bone("upper_arm.R", front_shoulder_R, front_shoulder_R + mathutils.Vector((0, 0, -0.5 * height)), parent=spine)
        # Front lower legs (elbow to paw)
        lower_front_L = create_bone("lower_arm.L", upper_front_L.tail, mathutils.Vector((upper_front_L.tail.x, upper_front_L.tail.y, min_z + 0.05 * height)), parent=upper_front_L)
        lower_front_R = create_bone("lower_arm.R", upper_front_R.tail, mathutils.Vector((upper_front_R.tail.x, upper_front_R.tail.y, min_z + 0.05 * height)), parent=upper_front_R)
        front_foot_L = create_bone("front_foot.L", lower_front_L.tail, mathutils.Vector((lower_front_L.tail.x, lower_front_L.tail.y, min_z)), parent=lower_front_L)
        front_foot_R = create_bone("front_foot.R", lower_front_R.tail, mathutils.Vector((lower_front_R.tail.x, lower_front_R.tail.y, min_z)), parent=lower_front_R)
        # Hind legs (attached near hip_center)
        back_hip_L = hip_center + mathutils.Vector(( hip_offset, 0, 0))
        back_hip_R = hip_center + mathutils.Vector(( -hip_offset, 0, 0))
        upper_hind_L = create_bone("thigh.L", back_hip_L, back_hip_L + mathutils.Vector((0, 0, -0.5 * height)), parent=spine)
        upper_hind_R = create_bone("thigh.R", back_hip_R, back_hip_R + mathutils.Vector((0, 0, -0.5 * height)), parent=spine)
        lower_hind_L = create_bone("shin.L", upper_hind_L.tail, mathutils.Vector((upper_hind_L.tail.x, upper_hind_L.tail.y, min_z + 0.05 * height)), parent=upper_hind_L)
        lower_hind_R = create_bone("shin.R", upper_hind_R.tail, mathutils.Vector((upper_hind_R.tail.x, upper_hind_R.tail.y, min_z + 0.05 * height)), parent=upper_hind_R)
        hind_foot_L = create_bone("hind_foot.L", lower_hind_L.tail, mathutils.Vector((lower_hind_L.tail.x, lower_hind_L.tail.y, min_z)), parent=lower_hind_L)
        hind_foot_R = create_bone("hind_foot.R", lower_hind_R.tail, mathutils.Vector((lower_hind_R.tail.x, lower_hind_R.tail.y, min_z)), parent=lower_hind_R)
        # (Optional) Tail bone
        #tail_base = hip_center + mathutils.Vector((0, -0.1 * length, pelvis_pos.z))
        #tail_end  = hip_center + mathutils.Vector((0, -0.3 * length, pelvis_pos.z))
        #tail_bone = create_bone("Tail", tail_base, tail_end, parent=spine)
    # End of bone creation
    
    # Exit Edit Mode for the armature
    bpy.ops.object.mode_set(mode='OBJECT')
    
    # Parenting mesh to armature with automatic weights:
    # Select armature and mesh for parenting operation
    rig_obj.select_set(True)
    mesh_obj.select_set(True)
    bpy.context.view_layer.objects.active = rig_obj  # armature must be active for parent_set
    # Perform parent with automatic weights (bone heat)
    bpy.ops.object.parent_set(type='ARMATURE_AUTO', xmirror=True, keep_transform=True)
    
    # Deselect objects
    rig_obj.select_set(False)
    mesh_obj.select_set(False)
    
    print(f"Rigging complete. Created armature: {rig_obj.name}")
    
    bpy.ops.export_scene.fbx(filepath="dog.fbx")


if __name__ == "__main__":
    if not sys.argv[1]:
        print("Need filepath!")
    else:
        auto_rig_glb(sys.argv[1])