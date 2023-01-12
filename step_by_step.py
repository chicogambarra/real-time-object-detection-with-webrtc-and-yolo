import argparse
import asyncio
import json
import logging
import os
import platform

from aiohttp import web
import cv2
import numpy as np
from av import VideoFrame
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaPlayer, MediaRelay


# Fazendo um passo a passo de acordo com o site: https://softwarescalability.com/editorial/real-time-object-detection-with-webrtc-and-yolo

model = "yolov7-tiny_480x640.onnx"


class YOLOVideoStreamTrack(VideoStreamTrack):
    """
    A video track thats returns camera track with annotated detected objects.
    """

    def __init__(self, conf_thres=0.7, iou_thres=0.5):
        super().__init__()  # don't forget this!
        self.conf_threshold = conf_thres
        self.iou_threshold = iou_thres

        video = cv2.VideoCapture(0)
        self.video = video

        # Initialize model
        self.net = cv2.dnn.readNet(model)
        input_shape = os.path.splitext(os.path.basename(model))[0].split('_')[-1].split('x')
        self.input_height = int(input_shape[0])
        self.input_width = int(input_shape[1])

        self.class_names = list(map(lambda x: x.strip(), open('coco.names', 'r').readlines()))
        self.colors = np.random.default_rng(3).uniform(0, 255, size=(len(self.class_names), 3))

        self.output_names = self.net.getUnconnectedOutLayersNames()

    def prepare_input(self, image):
        self.img_height, self.img_width = image.shape[:2]
        input_img = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        input_img = cv2.resize(input_img, (self.input_width, self.input_height))
        return input_img

    def detect(self, frame):
        input_img = self.prepare_input(frame)
        blob = cv2.dnn.blobFromImage(input_img, 1 / 255.0)
        # Perform inference on the image
        self.net.setInput(blob)
        # Runs the forward pass to get output of the output layers
        outputs = self.net.forward(self.output_names)

        boxes, scores, class_ids = self.process_output(outputs)
        return boxes, scores, class_ids

    def process_output(self, output):
        predictions = np.squeeze(output[0])

        # Filter out object confidence scores below threshold
        obj_conf = predictions[:, 4]
        predictions = predictions[obj_conf > self.conf_threshold]
        obj_conf = obj_conf[obj_conf > self.conf_threshold]

        # Multiply class confidence with bounding box confidence
        predictions[:, 5:] *= obj_conf[:, np.newaxis]

        # Get the scores
        scores = np.max(predictions[:, 5:], axis=1)

        # Filter out the objects with a low score
        valid_scores = scores > self.conf_threshold
        predictions = predictions[valid_scores]
        scores = scores[valid_scores]

        # Get the class with the highest confidence
        class_ids = np.argmax(predictions[:, 5:], axis=1)

        # Get bounding boxes for each object
        boxes = self.extract_boxes(predictions)

        # Apply non-maxima suppression to suppress weak, overlapping bounding boxes
        indices = cv2.dnn.NMSBoxes(boxes.tolist(), scores.tolist(), self.conf_threshold, self.iou_threshold)
        if len(indices) > 0:
            indices = indices.flatten()

        return boxes[indices], scores[indices], class_ids[indices]

    def rescale_boxes(self, boxes):
        input_shape = np.array([self.input_width, self.input_height, self.input_width, self.input_height])
        boxes = np.divide(boxes, input_shape, dtype=np.float32)
        boxes *= np.array([self.img_width, self.img_height, self.img_width, self.img_height])
        return boxes

    def extract_boxes(self, predictions):
        # Extract boxes from predictions
        boxes = predictions[:, :4]

        # Scale boxes to original image dimensions
        boxes = self.rescale_boxes(boxes)

        # Convert boxes to xywh format
        boxes_ = np.copy(boxes)
        boxes_[..., 0] = boxes[..., 0] - boxes[..., 2] * 0.5
        boxes_[..., 1] = boxes[..., 1] - boxes[..., 3] * 0.5
        return boxes_

    def draw_detections(self, frame, boxes, scores, class_ids):
        for box, score, class_id in zip(boxes, scores, class_ids):
            x, y, w, h = box.astype(int)
            color = self.colors[class_id]

            # Draw rectangle
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, thickness=2)
            label = self.class_names[class_id]
            label = f'{label} {int(score * 100)}%'
            cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.putText(frame, label, (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 1, color, thickness=2)

    def show_webcam(self, mirror=False):
        # video = cv2.VideoCapture(0)
        # self.video = video
        while True:
            ret_val, frame = self.video.read()
            if mirror:
                frame = cv2.flip(frame, 1)
            cv2.imshow('my webcam', frame)
            if cv2.waitKey(1) == 27:
                break  # esc to quit
        cv2.destroyAllWindows()