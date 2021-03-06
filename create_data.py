import copy
import pathlib
import pickle

import fire
import numpy as np
from skimage import io as imgio

import cv2

from second.core import box_np_ops
from second.core.point_cloud.point_cloud_ops import bound_points_jit
from second.data import kitti_common as kitti
from second.utils.progress_bar import list_bar as prog_bar

"""
Note: tqdm has problem in my system(win10), so use my progress bar
try:
    from tqdm import tqdm as prog_bar
except ImportError:
    from second.utils.progress_bar import progress_bar_iter as prog_bar
"""


def _read_imageset_file(path):
    with open(path, 'r') as f:
        lines = f.readlines()
    return [int(line) for line in lines]


def _calculate_num_points_in_gt(data_path, infos, relative_path, remove_outside=True, num_features=4):
    for info in infos:
        if relative_path:
            v_path = str(pathlib.Path(data_path) / info["velodyne_path"])
        else:
            v_path = info["velodyne_path"]
        points_v = np.fromfile(
            v_path, dtype=np.float32, count=-1).reshape([-1, num_features])
        rect = info['calib/R0_rect']
        Trv2c = info['calib/Tr_velo_to_cam']
        P2 = info['calib/P2']
        if remove_outside:
            points_v = box_np_ops.remove_outside_points(points_v, rect, Trv2c, P2,
                                                        info["img_shape"])

        # points_v = points_v[points_v[:, 0] > 0]
        annos = info['annos']
        num_obj = len([n for n in annos['name'] if n != 'DontCare'])
        # annos = kitti.filter_kitti_anno(annos, ['DontCare'])
        dims = annos['dimensions'][:num_obj]
        loc = annos['location'][:num_obj]
        rots = annos['rotation_y'][:num_obj]
        gt_boxes_camera = np.concatenate(
            [loc, dims, rots[..., np.newaxis]], axis=1)
        gt_boxes_lidar = box_np_ops.box_camera_to_lidar(
            gt_boxes_camera, rect, Trv2c)
        indices = box_np_ops.points_in_rbbox(points_v[:, :3], gt_boxes_lidar)
        num_points_in_gt = indices.sum(0)
        num_ignored = len(annos['dimensions']) - num_obj
        num_points_in_gt = np.concatenate(
            [num_points_in_gt, -np.ones([num_ignored])])
        annos["num_points_in_gt"] = num_points_in_gt.astype(np.int32)


def create_kitti_info_file(data_path,
                           save_path=None,
                           create_trainval=False,
                           relative_path=True):
    train_img_ids = _read_imageset_file("./data/ImageSets/train.txt")
    val_img_ids = _read_imageset_file("./data/ImageSets/val.txt")
    trainval_img_ids = _read_imageset_file("./data/ImageSets/trainval.txt")
    test_img_ids = _read_imageset_file("./data/ImageSets/test.txt")

    print("Generate info. this may take several minutes.")
    if save_path is None:
        save_path = pathlib.Path(data_path)
    else:
        save_path = pathlib.Path(save_path)
    kitti_infos_train = kitti.get_kitti_image_info(
        data_path,
        training=True,
        velodyne=True,
        calib=True,
        image_ids=train_img_ids,
        relative_path=relative_path)
    _calculate_num_points_in_gt(data_path, kitti_infos_train, relative_path)
    filename = save_path / 'kitti_infos_train.pkl'
    print(f"Kitti info train file is saved to {filename}")
    with open(filename, 'wb') as f:
        pickle.dump(kitti_infos_train, f)
    kitti_infos_val = kitti.get_kitti_image_info(
        data_path,
        training=True,
        velodyne=True,
        calib=True,
        image_ids=val_img_ids,
        relative_path=relative_path)
    _calculate_num_points_in_gt(data_path, kitti_infos_val, relative_path)
    filename = save_path / 'kitti_infos_val.pkl'
    print(f"Kitti info val file is saved to {filename}")
    with open(filename, 'wb') as f:
        pickle.dump(kitti_infos_val, f)
    """
    if create_trainval:
        kitti_infos_trainval = kitti.get_kitti_image_info(
            data_path,
            training=True,
            velodyne=True,
            calib=True,
            image_ids=trainval_img_ids,
            relative_path=relative_path)
        filename = save_path / 'kitti_infos_trainval.pkl'
        print(f"Kitti info trainval file is saved to {filename}")
        with open(filename, 'wb') as f:
            pickle.dump(kitti_infos_trainval, f)
    """
    filename = save_path / 'kitti_infos_trainval.pkl'
    print(f"Kitti info trainval file is saved to {filename}")
    with open(filename, 'wb') as f:
        pickle.dump(kitti_infos_train + kitti_infos_val, f)

    kitti_infos_test = kitti.get_kitti_image_info(
        data_path,
        training=False,
        label_info=False,
        velodyne=True,
        calib=True,
        image_ids=test_img_ids,
        relative_path=relative_path)
    filename = save_path / 'kitti_infos_test.pkl'
    print(f"Kitti info test file is saved to {filename}")
    with open(filename, 'wb') as f:
        pickle.dump(kitti_infos_test, f)


"""
semantic segmentation
"""


def _get_semantic_segmentation_result(image_idx, data_dir='/home/zhwang/semantic-segmentation/kitti_train_results'):
    filename = data_dir + '/pred_mask_00' + str(int(image_idx / 1000)) + str(int(image_idx % 1000 / 100)) + str(
        int(image_idx % 100 / 10)) + str(int(image_idx % 10)) + '.png'
    rgb_image_label = cv2.imread(filename)  # I.shape=(375,1242,3),rgb 3 channels
    image_label = rgb_image_label[:, :, 0]  # (375,1242)
    image_label[np.where(image_label == 24)] = 80  # person
    image_label[np.where(image_label == 25)] = 120  # rider
    image_label[np.where(image_label == 33)] = 160  # bicycle
    image_label[np.where(image_label == 26)] = 40  # car
    image_label[np.where(image_label <= 34)] = 0  # background
    image_label = image_label / 40
    return image_label


"""
point cloud shape from (x,y,z,r) to (x,y,z,r,class_score(background->0,car->1,pedestrian->2,cyclist->3))
"""


def _add_class_score(image_label, points_v, rect, Trv2c, P2):
    points_xyz = points_v[:, :3]  # x,y,z (num_points,3)
    reflectance = points_v[:, 3:]  # (num_points,1)
    # print(points_xyz.shape)
    points_v_to_c = box_np_ops.lidar_to_camera(points_xyz, rect, Trv2c)  # x,y,z in camera (num_points,3)
    # print(points_v_to_c.shape)
    points_v_to_image = box_np_ops.project_to_image(points_v_to_c, P2)  # u,v in image (num_points,2)
    # print(points_v_to_image.shape)
    num_points = len(points_v_to_image)
    class_score = np.zeros([num_points, 1])
    i = 0
    list = []
    for num in range(num_points):
        u = int(round(points_v_to_image[num][1]))
        v = int(round(points_v_to_image[num][0]))
        if u < image_label.shape[0]:
            class_score[num][0] = image_label[u][v]  # (num_points,1)
            # if class_score[num][0] != 0:
            #     i = i + 1
            #     list.append(num)
    # print(i)
    # print(list)
    points_v = np.concatenate((points_xyz, reflectance, class_score), axis=1)  # (num_points,5)
    # print(points_v.shape)
    """
    mapping all points painted with the bike class within a 1m radius of a rider to the cyclist class; the rest to background.
    """
    rider = []
    bicycle = []
    for num in range(len(points_v)):
        if points_v[num][4] == 4:
            bicycle.append(num)
        elif points_v[num][4] == 3:
            rider.append(num)
    for i in bicycle:
        xb, yb, zb = points_v[i][:3]
        for j in rider:
            xr, yr, zr = points_v[j][:3]
            if np.square(xb - xr) + np.square(yb - yr) + np.square(zb - zr) <= 1:
                points_v[i][4] = 3
                break
            else:
                points_v[i][4] = 0
    points_v = points_v.astype(dtype=np.float32)
    return points_v


def _create_reduced_point_cloud(data_path='/mrtstorage/datasets/kitti/object_detection',
                                info_path='/home/zhwang/second.pytorch/second/data/sets/kitti_second'
                                          '/kitti_infos_train.pkl',
                                save_path='/home/zhwang/second.pytorch/second/data/sets/kitti_second/training'
                                          '/velodyne_reduced',
                                back=False):
    with open(info_path, 'rb') as f:
        kitti_infos = pickle.load(f)
    for info in prog_bar(kitti_infos):
        v_path = info['velodyne_path']
        v_path = pathlib.Path(data_path) / v_path
        points_v = np.fromfile(
            str(v_path), dtype=np.float32, count=-1).reshape([-1, 4])
        rect = info['calib/R0_rect']
        P2 = info['calib/P2']
        Trv2c = info['calib/Tr_velo_to_cam']
        # first remove z < 0 points
        # keep = points_v[:, -1] > 0
        # points_v = points_v[keep]
        # then remove outside.
        if back:
            points_v[:, 0] = -points_v[:, 0]
        points_v = box_np_ops.remove_outside_points(points_v, rect, Trv2c, P2,
                                                    info["img_shape"])
        # print(points_v.dtype)
        """
        fusion camera data to lidar
        """
        image_idx = info['image_idx']
        image_label = _get_semantic_segmentation_result(image_idx,
                                                        data_dir='/home/zhwang/semantic-segmentation'
                                                                 '/kitti_train_results')

        points_v = _add_class_score(image_label, points_v, rect, Trv2c, P2)
        # print(points_v.dtype)
        if save_path is None:
            save_filename = v_path.parent.parent / (v_path.parent.stem + "_reduced") / v_path.name
            # save_filename = str(v_path) + '_reduced'
            if back:
                save_filename += "_back"
        else:
            save_filename = str(pathlib.Path(save_path) / v_path.name)
            if back:
                save_filename += "_back"
        with open(save_filename, 'w') as f:
            points_v.tofile(f)


def create_reduced_point_cloud(data_path,
                               train_info_path=None,
                               val_info_path=None,
                               test_info_path=None,
                               save_path=None,
                               with_back=False):
    if train_info_path is None:
        train_info_path = pathlib.Path(data_path) / 'kitti_infos_train.pkl'
    if val_info_path is None:
        val_info_path = pathlib.Path(data_path) / 'kitti_infos_val.pkl'
    if test_info_path is None:
        test_info_path = pathlib.Path(data_path) / 'kitti_infos_test.pkl'

    _create_reduced_point_cloud(data_path, train_info_path, save_path)
    _create_reduced_point_cloud(data_path, val_info_path, save_path)
    _create_reduced_point_cloud(data_path, test_info_path, save_path)
    if with_back:
        _create_reduced_point_cloud(
            data_path, train_info_path, save_path, back=True)
        _create_reduced_point_cloud(
            data_path, val_info_path, save_path, back=True)
        _create_reduced_point_cloud(
            data_path, test_info_path, save_path, back=True)


def create_groundtruth_database(data_path='/mrtstorage/datasets/kitti/object_detection',
                                info_path='/home/zhwang/second.pytorch/second/data/sets/kitti_second'
                                          '/kitti_infos_train.pkl',
                                used_classes=None,
                                database_save_path='/home/zhwang/second.pytorch/second/data/sets/kitti_second'
                                                   '/gt_database',
                                db_info_save_path='/home/zhwang/second.pytorch/second/data/sets/kitti_second'
                                                  '/kitti_dbinfos_train.pkl',
                                relative_path=True,
                                lidar_only=False,
                                bev_only=False,
                                coors_range=None):
    root_path = pathlib.Path(data_path)
    if info_path is None:
        info_path = root_path / 'kitti_infos_train.pkl'
    if database_save_path is None:
        database_save_path = root_path / 'gt_database'
    else:
        database_save_path = pathlib.Path(database_save_path)
    if db_info_save_path is None:
        db_info_save_path = root_path / "kitti_dbinfos_train.pkl"
    database_save_path.mkdir(parents=True, exist_ok=True)
    with open(info_path, 'rb') as f:
        kitti_infos = pickle.load(f)
    all_db_infos = {}
    if used_classes is None:
        used_classes = list(kitti.get_classes())
        used_classes.pop(used_classes.index('DontCare'))
    for name in used_classes:
        all_db_infos[name] = []
    group_counter = 0
    for info in prog_bar(kitti_infos):
        velodyne_path = info['velodyne_path']
        if relative_path:
            # velodyne_path = str(root_path / velodyne_path) + "_reduced"
            velodyne_path = str(root_path / velodyne_path)
        num_features = 4
        if 'pointcloud_num_features' in info:
            num_features = info['pointcloud_num_features']
        points = np.fromfile(
            velodyne_path, dtype=np.float32, count=-1).reshape([-1, num_features])

        image_idx = info["image_idx"]
        rect = info['calib/R0_rect']
        P2 = info['calib/P2']
        Trv2c = info['calib/Tr_velo_to_cam']
        if not lidar_only:
            points = box_np_ops.remove_outside_points(points, rect, Trv2c, P2,
                                                      info["img_shape"])
        """
        fusion camera data to lidar
        """
        image_label = _get_semantic_segmentation_result(image_idx, data_dir='/home/zhwang/semantic-segmentation'
                                                                            '/kitti_train_results')
        points = _add_class_score(image_label, points, rect, Trv2c, P2)

        annos = info["annos"]
        names = annos["name"]
        bboxes = annos["bbox"]
        difficulty = annos["difficulty"]
        gt_idxes = annos["index"]
        num_obj = np.sum(annos["index"] >= 0)
        rbbox_cam = kitti.anno_to_rbboxes(annos)[:num_obj]
        rbbox_lidar = box_np_ops.box_camera_to_lidar(rbbox_cam, rect, Trv2c)
        if bev_only:  # set z and h to limits
            assert coors_range is not None
            rbbox_lidar[:, 2] = coors_range[2]
            rbbox_lidar[:, 5] = coors_range[5] - coors_range[2]

        group_dict = {}
        group_ids = np.full([bboxes.shape[0]], -1, dtype=np.int64)
        if "group_ids" in annos:
            group_ids = annos["group_ids"]
        else:
            group_ids = np.arange(bboxes.shape[0], dtype=np.int64)
        point_indices = box_np_ops.points_in_rbbox(points, rbbox_lidar)
        for i in range(num_obj):
            filename = f"{image_idx}_{names[i]}_{gt_idxes[i]}.bin"
            filepath = database_save_path / filename
            gt_points = points[point_indices[:, i]]

            gt_points[:, :3] -= rbbox_lidar[i, :3]
            with open(filepath, 'w') as f:
                gt_points.tofile(f)
            if names[i] in used_classes:
                if relative_path:
                    db_path = str(database_save_path.stem + "/" + filename)
                else:
                    db_path = str(filepath)
                db_info = {
                    "name": names[i],
                    "path": db_path,
                    "image_idx": image_idx,
                    "gt_idx": gt_idxes[i],
                    "box3d_lidar": rbbox_lidar[i],
                    "num_points_in_gt": gt_points.shape[0],
                    "difficulty": difficulty[i],
                    # "group_id": -1,
                    # "bbox": bboxes[i],
                }

                local_group_id = group_ids[i]
                # if local_group_id >= 0:
                if local_group_id not in group_dict:
                    group_dict[local_group_id] = group_counter
                    group_counter += 1
                db_info["group_id"] = group_dict[local_group_id]
                if "score" in annos:
                    db_info["score"] = annos["score"][i]
                all_db_infos[names[i]].append(db_info)
    for k, v in all_db_infos.items():
        print(f"load {len(v)} {k} database infos")

    with open(db_info_save_path, 'wb') as f:
        pickle.dump(all_db_infos, f)


if __name__ == '__main__':
    fire.Fire()
