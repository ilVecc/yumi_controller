#!/usr/bin/env python3

# This is a super basic "simulator" or more like it integrates the velocity commands at 250 hz

import numpy as np

import rospy
import threading

from sensor_msgs.msg import JointState
from std_msgs.msg import Float64MultiArray
from abb_egm_msgs.msg import EGMState, EGMChannelState
from abb_rapid_sm_addin_msgs.srv import SetSGCommand
from abb_robot_msgs.srv import TriggerWithResultCode


class YumiJointState(object):
    def __init__(self,
        jointPosition=np.array([1.0, -2.0, -1.2, 0.6, -2.0, 1.0, 0.0, -1.0, -2.0, 1.2, 0.6, 2.0, 1.0, 0.0]),
        jointVelocity=np.zeros(14),
        gripperRightPosition=np.zeros(2),
        gripperLeftPosition=np.zeros(2),
        gripperRightVelocity=np.zeros(2),
        gripperLeftVelocity=np.zeros(2)
    ):
        self.jointPosition = jointPosition  # only arm not gripper
        self.jointVelocity = jointVelocity  # only arm not gripper
        self.gripperRightPosition = gripperRightPosition
        self.gripperLeftPosition = gripperLeftPosition
        self.gripperRightVelocity = gripperRightVelocity
        self.gripperLeftVelocity = gripperLeftVelocity
    
    def velocity(self):
        return np.hstack([self.jointVelocity,
                          self.gripperRightVelocity,
                          self.gripperLeftVelocity])
    
    def position(self):
        return np.hstack([self.jointPosition,
                          self.gripperRightPosition,
                          self.gripperLeftPosition])

    def UpdatePose(self, pose):
        self.jointPosition = pose[0:14]
        self.gripperRightPosition = pose[14:16]
        self.gripperLeftPosition = pose[16:18]

    def UpdateVelocity(self, vel):
        self.jointVelocity = vel[0:14]
        self.gripperRightVelocity = vel[14:16]
        self.gripperLeftVelocity = vel[16:18]


class Simulator(object):
    def __init__(self):
        self.updateRate = 250 #hz
        self.dt = 1/self.updateRate
        self.jointState = YumiJointState(
            jointPosition=np.array([ 0.0, -2.270, -2.356, 0.524, 0.0, 0.670, 0.0,
                                     0.0, -2.270,  2.356, 0.524, 0.0, 0.670, 0.0]))

        self.lock = threading.Lock()

        upperArmLimit = np.radians([ 168.5,   43.5,  168.5,     80,  290, 138,  229])
        lowerArmLimit = np.radians([-168.5, -143.5, -168.5, -123.5, -290, -88, -229])

        self.jointPositionBoundUpper = np.hstack([upperArmLimit, upperArmLimit, np.array([0.025, 0.025, 0.025, 0.025])])
        self.jointPositionBoundLower = np.hstack([lowerArmLimit, lowerArmLimit, np.array([-0.0, -0.0, -0.0, -0.0])])
        self.targetGripperPos = np.array([0.0, 0.0])
        # create ros service for grippers
        rospy.Service("/yumi/rws/sm_addin/set_sg_command", SetSGCommand, self.receiveGripperCommand)
        rospy.Service("/yumi/rws/sm_addin/run_sg_routine", TriggerWithResultCode, self.setGripperCommand)
        # name of gripper joints in urdf
        self.jointNamesGrippers = ["gripper_l_joint", "gripper_l_joint_m", "gripper_r_joint", "gripper_r_joint_m"]
        self.gripperPosition = np.array([0.0,0.0]) # used to store gripper commands until they are used

    def callback(self, data):
        vel = np.asarray(data.data)
        vel = np.hstack([vel[7:14], vel[0:7], np.zeros(4)])
        self.lock.acquire()
        self.jointState.UpdateVelocity(vel)
        self.lock.release()

    def update(self):
        # updates the pose
        self.lock.acquire()
        pose = self.jointState.position() + self.jointState.velocity()*self.dt
        rightGripper = self.jointState.position()[14:16]
        leftGripper = self.jointState.position()[16:18]
        self.lock.release()

        velRightGripper = (self.targetGripperPos[0] - rightGripper)*self.dt
        pose[14:16] = rightGripper + velRightGripper
        velLeftGripper = (self.targetGripperPos[1] - leftGripper)*self.dt
        pose[16:18] = leftGripper + velLeftGripper

        # hard joint limits 
        pose = np.clip(pose, self.jointPositionBoundLower, self.jointPositionBoundUpper)
        self.jointState.UpdatePose(pose=pose)

    def receiveGripperCommand(self, SetSGCommand):
        # callback for gripper set_sg_command service, only 3 functionalities emulated, move to, grip in and grip out.
        # index for left gripper task
        if SetSGCommand.task == "T_ROB_R":
            index_a = 0
        # index for the right gripper
        elif SetSGCommand.task == "T_ROB_L":
            index_a = 1
        else:
            return [2, ""]  # returns failure state as service is finished

        if SetSGCommand.command == 5:  # move to
            self.gripperPosition[index_a] = SetSGCommand.target_position/1000  # convert mm to meters

        elif SetSGCommand.command == 6:  # grip in
            self.gripperPosition[index_a] = 0
        elif SetSGCommand.command == 7:  # grip out
            self.gripperPosition[index_a] = 0.025
        else:
            return [2, ""]  # returns failure state as service is finished

        return [1, ""]  # returns success state as service is finished

    def setGripperCommand(self, SetSGCommand):
        # callback for run_sg_routine, runs the gripper commands, i.e. grippers wont move before this service is called.
        self.targetGripperPos = np.copy(self.gripperPosition)
        return [1, ""]


def main():
    # starting ROS node and subscribers
    rospy.init_node("yumi_simulator", anonymous=True)
     
    pub = rospy.Publisher("/yumi/egm/joint_states", JointState, queue_size=1)
    pub_egm_state = rospy.Publisher("/yumi/egm/egm_states", EGMState, queue_size=1)

    simulator = Simulator()

    rospy.Subscriber("/yumi/egm/joint_group_velocity_controller/command", Float64MultiArray, simulator.callback, queue_size=1, tcp_nodelay=True)

    rate = rospy.Rate(simulator.updateRate) 

    msg = JointState(
        name = [
            "yumi_robr_joint_1", "yumi_robr_joint_2", "yumi_robr_joint_3", "yumi_robr_joint_4", "yumi_robr_joint_5", "yumi_robr_joint_6", "yumi_robr_joint_7",  
            "yumi_robl_joint_1", "yumi_robl_joint_2", "yumi_robl_joint_3", "yumi_robl_joint_4", "yumi_robl_joint_5", "yumi_robl_joint_6", "yumi_robl_joint_7", 
            "gripper_r_joint", "gripper_r_joint_m", "gripper_l_joint", "gripper_l_joint_m"]
    )
    msg_egm_state = EGMState(
        egm_channels = [
            EGMChannelState(active=True), 
            EGMChannelState(active=True)]
    )

    seq = 1
    while not rospy.is_shutdown():
        simulator.update()
        msg.header.stamp = rospy.Time.now()
        msg.header.seq = seq
        msg.position = simulator.jointState.position().tolist()
        msg.velocity = simulator.jointState.velocity().tolist()
        
        pub.publish(msg)
        pub_egm_state.publish(msg_egm_state)
        
        rate.sleep()
        seq += 1


if __name__ == "__main__":
    main()
