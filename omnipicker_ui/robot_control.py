"""Preview plans and explicit joint/tool pose application for the integrated UI."""
from copy import deepcopy
import math
import secrets
import time
from .validation import number
from .kinematics import cad_limits, tool_target


def data(reply,name):
    if not isinstance(reply,(list,tuple)) or len(reply)!=2 or reply[0]!=0:
        raise RuntimeError(f'{name} 실패: {reply}')
    return reply[1]


def vector(values,name='위치'):
    if not isinstance(values,(list,tuple)) or len(values)!=6: raise ValueError(f'{name}: 6개 값 필요')
    return [number(x,-math.inf,math.inf,name) for x in values]


class RobotControl:
    def init_robot_control(self):
        self.joint_limits=cad_limits()
        self.joint_limit_source='제조사 CAD'
        self.active_tool=self.active_user=0
        self.tcp_offset=[0.]*6
        self.plans={}
        self.external_bus_pause=False

    def motion_limits(self):
        return dict(joint_step=[0,None],robot_speed=[1,100],translation_step=[.1,10],rotation_step=[0,None],
            tool_translation=[None,None],tool_rotation=[None,None],step_min_exclusive=True,joints=self.joint_limits,
            joint_source=self.joint_limit_source,gripper_position=[0,255],
            gripper_speed=[self.gripper_limits.speed_min,self.gripper_limits.speed_max],
            gripper_torque=[self.gripper_limits.torque_min,self.gripper_limits.torque_max],gripper_acceleration=[1,255])

    def read_motion_configuration(self):
        values=data(self.client.GetJointSoftLimitDeg(),'관절 한계')
        if not isinstance(values,(list,tuple)) or len(values)!=12: raise RuntimeError('관절 한계 응답 오류')
        pairs=[[number(values[i],-1000,1000,'관절 한계'),number(values[i+1],-1000,1000,'관절 한계')] for i in range(0,12,2)]
        if any(lo>=hi for lo,hi in pairs): raise RuntimeError('관절 한계 순서 오류')
        self.joint_limits=pairs;self.joint_limit_source='FR3 소프트 한계'
        self.active_tool=int(data(self.client.GetActualTCPNum(),'활성 툴'))
        self.active_user=int(data(self.client.GetActualWObjNum(),'활성 좌표계'))
        self.tcp_offset=vector(data(self.client.GetTCPOffset(),'TCP 오프셋'))

    def validate_joint_target(self,target):
        target=vector(target,'목표 관절')
        for i,(v,(lo,hi)) in enumerate(zip(target,self.joint_limits)):
            if not lo<=v<=hi: raise ValueError(f'J{i+1}: {lo:g}~{hi:g}°')
        return target

    def preview_robot(self,kind,joints=None,delta=None,reference=None,tool=0,user=0):
        if kind not in ('joint','tool'): raise ValueError('미리보기 모드 오류')
        tool=number(tool,0,14,'툴',True);user=number(user,0,14,'좌표계',True)
        with self.lock:
            if not self.snapshot()['robot']['connected'] or self.robot_busy: raise RuntimeError('FR3 연결·정지 상태 확인 필요')
        self.read_motion_configuration()
        if data(self.client.robot.GetProgramState(),'프로그램 상태')!=1: raise RuntimeError('다른 FR3 프로그램 실행 중')
        current=vector(data(self.client.GetActualJointPosDegree(),'현재 관절'))
        base=current if reference is None else self.validate_joint_target(reference)
        if max(abs(a-b) for a,b in zip(base,current))>.5: raise RuntimeError('현재 위치 변경 · 현재 → 목표로 갱신하세요.')
        if kind=='joint':
            target=self.validate_joint_target(joints)
            target_pose=vector(data(self.client.GetForwardKin(target),'정기구학'))
            reference_pose=vector(data(self.client.GetForwardKin(base),'기준 TCP'))
        else:
            delta=vector(delta,'툴 이동량')
            if tool!=self.active_tool or user!=0 or self.active_user!=0:
                raise RuntimeError(f'툴 이동: 활성 툴 {self.active_tool} 사용, 작업좌표계 0 필요')
            reference_pose=vector(data(self.client.GetForwardKin(base),'기준 TCP'))
            target_pose=vector(tool_target(reference_pose,delta),'목표 TCP')
            target=self.validate_joint_target(data(self.client.GetInverseKinRef(0,target_pose,base),'역기구학'))
        plan=dict(id=secrets.token_urlsafe(18),kind=kind,joints=target,pose=target_pose,reference=base,
                  tool=tool,user=user,reference_pose=reference_pose,tcp_offset=self.tcp_offset[:],epoch=self.epoch,expires=time.monotonic()+45)
        with self.lock:
            self.plans={k:v for k,v in self.plans.items() if v['expires']>time.monotonic()}
            if len(self.plans)>=32:self.plans.pop(next(iter(self.plans)))
            self.plans[plan['id']]=plan
        return deepcopy({k:v for k,v in plan.items() if k not in ('epoch','expires')})|dict(valid_seconds=45)

    def apply_robot_plan(self,plan_id,velocity=5):
        velocity=number(velocity,1,100,'속도')
        with self.lock:
            self.require_enabled()
            if self.robot_busy or self.robot_fault or self.external_bus_pause: raise RuntimeError('이동/설정 처리 중 또는 이전 응답 불확실')
            plan=self.plans.pop(str(plan_id),None)
            if plan is None: raise RuntimeError('미리보기 갱신 필요 · 사용/만료된 목표')
            if plan['expires']<time.monotonic() or plan['epoch']!=self.epoch: raise RuntimeError('미리보기 만료 · 갱신 필요')
            self.robot_busy=True
        dispatched=False
        try:
            if data(self.client.robot.GetProgramState(),'프로그램 상태')!=1: raise RuntimeError('다른 FR3 프로그램 실행 중')
            current=vector(data(self.client.GetActualJointPosDegree(),'현재 관절'))
            if max(abs(a-b) for a,b in zip(current,plan['reference']))>.5: raise RuntimeError('현재 위치 변경 · 미리보기 갱신 필요')
            self.read_motion_configuration()
            self.validate_joint_target(plan['joints'])
            if plan['kind']=='tool' and (self.active_tool!=plan['tool'] or self.active_user!=0 or self.tcp_offset!=plan['tcp_offset']):
                raise RuntimeError('활성 툴/좌표계 변경 · 미리보기 갱신 필요')
            with self.lock:
                self.require_enabled()
                if self.epoch!=plan['epoch']: raise RuntimeError('이동 취소됨')
                self.target_joints=plan['joints'][:]
            dispatched=True
            with self.fr3.timeout(120):
                if plan['kind']=='joint':
                    reply=self.client.MoveJ(plan['joints'],tool=plan['tool'],user=plan['user'],vel=velocity,blendT=-1.)
                else:
                    reply=self.client.MoveL(plan['pose'],tool=plan['tool'],user=0,joint_pos=plan['joints'],vel=velocity,blendR=-1.)
            code=reply[0] if isinstance(reply,(tuple,list)) else reply
            if code!=0: raise RuntimeError(f'목표 적용 실패: {reply}')
            self.event('FR3','관절 목표 적용' if plan['kind']=='joint' else '툴 기준 목표 적용')
            return dict(target=plan['joints'],pose=plan['pose'],kind=plan['kind'])
        except Exception:
            if dispatched:self.robot_fault=True
            self.disarm()
            raise
        finally:self.robot_busy=False
