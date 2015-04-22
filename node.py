from datetime import datetime
import heapq
from math import sqrt
from math import ceil
import select
import sys
import time
import threading
from threading import Thread

import config
from enum_type import STATE
from enum_type import MSG_TYPE
from message import Message
import utils

global CS_INT, NEXT_REQ, TOT_EXEC_TIME, OPTION

RECV_BUFFER = 4096
CS_INT = 3
NEXT_REQ = 4
TOT_EXEC_TIME = 20
OPTION = 1

class ServerThread(Thread):
	def __init__(self, node):
		Thread.__init__(self)
		self._node = node
		self._connectionList = []
		self._serverSocket = utils.CreateServerSocket(config.NODE_PORT[self._node.NodeID])
		self._connectionList.append(self._serverSocket)

	def run(self):
		self._update()

	def _update(self):
		while True:
			read_sockets,write_sockets,error_sockets = select.select(self._connectionList,[],[])
			for read_socket in read_sockets:
				if read_socket == self._serverSocket:
					conn, addr = read_socket.accept()
					self._connectionList.append(conn)
				else:
					try:
						msg = read_socket.recv(RECV_BUFFER)
						self._processMessage(Message.ToMessage(msg))
					except:
						self._connectionList.remove(read_socket)
						read_socket.close()
						continue
		self._serverSocket.close()

	def _processMessage(self, msg):
		'''sys.stdout.write("{time} {node_id} {sender} {msg_type}\n".format(
			time=datetime.now().time().strftime("%H:%M:%S:%f"),
			node_id=self._node.NodeID,
			sender=msg.src,
			msg_type=msg.msg_type.ToStr(),
			))'''
		self._node.LamportTS = max(self._node.LamportTS + 1, msg.ts)
		if msg.msg_type == MSG_TYPE.REQUEST:
			self._onRequest(msg)
		elif msg.msg_type == MSG_TYPE.GRANT:
			self._onGrant(msg)
		elif msg.msg_type == MSG_TYPE.RELEASE:
			self._onRelease(msg)
		elif msg.msg_type == MSG_TYPE.FAIL:
			self._onFail(msg)
		elif msg.msg_type == MSG_TYPE.INQUIRE:
			self._onInquire(msg)
		elif msg.msg_type == MSG_TYPE.YIELD:
			self._onYield(msg)
		else:
			pass

	def _onRequest(self, request_msg):
		if self._node.State == STATE.HELD:
			heapq.heappush(self._node.RequestQueue,
				(request_msg.ts, request_msg))
		else:
			if self._node.HasVoted:
				heapq.heappush(self._node.RequestQueue,
					(request_msg.ts, request_msg))
				response_msg = Message(src=self._node.NodeID, ts=self._node.LamportTS)
				if request_msg.ts < self._node.VotedRequest.ts and not self._node.HasInquired:
					response_msg.SetType(MSG_TYPE.INQUIRE)
					response_msg.SetDest(self._node.VotedRequest.src)
				else:
					response_msg.SetType(MSG_TYPE.FAIL)
					response_msg.SetDest(request_msg.src)
				self._node.Client.SendMessage(response_msg, response_msg.dest)
			else:
				self._grantRequest(request_msg)

	def _onRelease(self, release_msg=None):
		self._node.HasInquired = False
		if self._node.RequestQueue:
			next_request = heapq.heappop(RequestQueue)
			self._grantRequest(next_request)
		else:
			self._node.HasVoted = False
			self._node.VotedRequest = None

	def _grantRequest(self, request_msg):
		grant_msg = Message(
			MSG_TYPE.GRANT,
			self._node.NodeID,
			request_msg.src,
			self._node.LamportTS,
			)
		self._node.Client.SendMessage(grant_msg, grant_msg.dest)
		self._node.HasVoted = True
		self._node.VotedRequest = request_msg

	def _onGrant(self, grant_msg):
		self._node.VotingSet[grant_msg.src] = grant_msg
		self._node.NumVotesReceived += 1
		#sys.stdout.write("votes: {votes}\n".format(votes=self._node.NumVotesReceived))

	def _onFail(self, fail_msg):
		self._node.VotingSet[fail_msg.src] = fail_msg

	def _onInquire(self, inquire_msg):
		if self._node.State != STATE.HELD:
			self._node.VotingSet[inquire_msg.src] = None
			yield_msg = Message(MSG_TYPE.YIELD,
				self._node.NodeID,
				inquire_msg.src,
				self._node.LamportTS,
				)
			self._node.Client.SendMessage(yield_msg, yield_msg.dest)

	def _onYield(self, yield_msg):
		heapq.heappush(self._node.RequestQueue, 
			(self._node.VotedRequest.ts,
				self._node.VotedRequest,
				))
		self._onRelease()


class ClientThread(Thread):
	def __init__(self, node):
		Thread.__init__(self)
		self._node = node
		self._clientSockets = [utils.CreateClientSocket() for i in xrange(config.NUM_NODE)]

	def run(self):
		if self._node.NodeID == 0:
			self._update()
		else:
			pass

	def _update(self):
		cnt = 0
		while True:
			if cnt > 1:
				break
			self._node.SignalRequestCS.wait()
			self._node.RequestCS()
			self._node.SignalEnterCS.wait()
			self._node.EnterCS()
			self._node.SignalExitCS.wait()
			self._node.ExitCS()
			cnt += 1

	def SendMessage(self, msg, dest):
		sys.stdout.write("{src}: sending {msg} to {dest}\n".format(
			src=self._node.NodeID,
			msg=msg.msg_type.ToStr(), 
			dest=dest,
			))
		self._clientSockets[dest].send(msg.ToJSON())

	def Multicast(self, msg, group):
		for dest in group:
			msg.SetDest(dest)
			self.SendMessage(msg, dest)

	def BuildConnection(self, num_node):
		for i in xrange(num_node):
			self._clientSockets[i].connect(('localhost', config.NODE_PORT[i]))
			print "{src} connects with {dest}!".format(
				src=self._node.NodeID, 
				dest=i,
				)


class CheckerThread(Thread):
	def __init__(self, node):
		Thread.__init__(self)
		self._node = node

	def run(self):
		self._update()

	def _update(self):
		global CS_INT, NEXT_REQ
		while True:
			curr_time = time.time()
			if self._node.State == STATE.RELEASE and utils.TimeElapsed(self._node.TimeExitCS, curr_time, 1) >= NEXT_REQ:
				self._node.SignalRequestCS.set()
			if self._node.State == STATE.REQUEST and self._node.NumVotesReceived == len(self._node.VotingSet):
				self._node.SignalEnterCS.set()
			if self._node.State == STATE.HELD and utils.TimeElapsed(self._node.TimeEnterCS, curr_time, 1) >= CS_INT:
				self._node.SignalExitCS.set()


class Node(object):
	def __init__(self, node_id):
		self.NodeID = node_id
		self.State = STATE.INIT
		
		self.LamportTS = 0
		
		#attributes as a voter (receive & process request)
		self.HasVoted = False
		self.VotedRequest = None
		self.RequestQueue = [] #a priority queue (key = lamportTS, value = request)

		#attributess as a proposer (propose & send request)
		self.VotingSet = self._createVotingSet()
		self.NumVotesReceived = 0	
		self.HasInquired = False

		self.Server = ServerThread(self)
		self.Server.start()
		self.Client = ClientThread(self)
		self.Checker = CheckerThread(self)

		#Event signals
		self.SignalRequestCS = threading.Event()
		self.SignalRequestCS.set()
		self.SignalEnterCS = threading.Event()
		self.SignalExitCS = threading.Event()

		#time elapsed in different stages (in millionsecondes)
		self.TimeEnterCS = 0
		self.TimeExitCS = 0

		print "Init node {node_id} completed!".format(node_id=self.NodeID)

	def _createVotingSet(self):
		voting_set = dict()
		mat_k = int(ceil(sqrt(config.NUM_NODE)))
		row_id, col_id = int(self.NodeID / mat_k), int(self.NodeID % mat_k)
		for i in xrange(mat_k):
			voting_set[mat_k * row_id + i] = None
			voting_set[col_id + mat_k * i] = None
		return voting_set

	def _resetVotingSet(self):
		for voter in self.VotingSet:
			self.VotingSet[voter] = None

	def RequestCS(self):
		self.State = STATE.REQUEST
		self.LamportTS += 1
		sys.stdout.write("{src}: request CS at {t}\n".format(
			src=self.NodeID, 
			t=datetime.now().time(),
			))
		request_msg = Message(
			msg_type=MSG_TYPE.REQUEST,
			src=self.NodeID,
			ts=self.LamportTS,
			)
		self.Client.Multicast(request_msg, self.VotingSet.keys())
		self.SignalRequestCS.clear()

	def EnterCS(self):
		self.State = STATE.HELD
		self.LamportTS += 1
		self.TimeEnterCS = time.time() 
		sys.stdout.write("{src}: enter CS at {t}\n".format(
			src=self.NodeID, 
			t=datetime.now().time(),
			))
		self.SignalEnterCS.clear()

	def ExitCS(self):
		self.State = STATE.RELEASE
		self.LamportTS += 1
		self.NumVotesReceived = 0
		self._resetVotingSet()
		self.TimeExitCS = time.time()
		sys.stdout.write("{src}: exit CS at {t}\n".format(
			src=self.NodeID,
			t=datetime.now().time(),
			))
		release_msg = Message(
			msg_type=MSG_TYPE.RELEASE,
			src=self.NodeID,
			ts=self.LamportTS,
			)
		self.Client.Multicast(release_msg, self.VotingSet.keys())
		self.SignalExitCS.clear()

	def BuildConnection(self, num_node):
		self.Client.BuildConnection(num_node)

	def Run(self):
		self.Client.start()
		self.Checker.start()
			