'''
Copyright (c) 2019 Uber Technologies, Inc.

Licensed under the Uber Non-Commercial License (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at the root directory of this project. 

See the License for the specific language governing permissions and
limitations under the License.
'''
'''
# TODO Add documentation
'''

__author__ = "Alexandros Papangelis"

from .. import Policy
from DialogueManagement.Policy.HandcraftedPolicy import HandcraftedPolicy
from Ontology import Ontology, DataBase
from Dialogue.Action import DialogueAct, DialogueActItem, Operator
from Dialogue.State import SlotFillingDialogueState, DummyDialogueState
from UserSimulator.AgendaBasedUserSimulator.AgendaBasedUS import AgendaBasedUS
from copy import deepcopy

import tensorflow as tf
import numpy as np
import random
import os


class SupervisedMultiAction_Policy(Policy.Policy):

    def __init__(self, ontology, database, agent_id=0, agent_role='system', domain=None):
        super(SupervisedMultiAction_Policy, self).__init__()

        self.agent_id = agent_id
        self.agent_role = agent_role

        # True for greedy, False for stochastic
        self.IS_GREEDY_POLICY = True

        self.ontology = None
        if isinstance(ontology, Ontology.Ontology):
            self.ontology = ontology
        else:
            raise ValueError('Supervised Policy: Unacceptable ontology type %s ' % ontology)

        self.database = None
        if isinstance(database, DataBase.DataBase):
            self.database = database
        else:
            raise ValueError('Supervised Policy: Unacceptable database type %s ' % database)

        self.policy_path = None

        self.policy_net = None
        self.tf_scope = "policy_" + self.agent_role + '_' + str(self.agent_id)
        self.sess = None

        self.warmup_policy = None
        self.warmup_simulator = None

        # Default value
        self.is_training = True

        # Extract lists of slots that are frequently used
        self.informable_slots = deepcopy(list(self.ontology.ontology['informable'].keys()))
        self.requestable_slots = deepcopy(self.ontology.ontology['requestable'] + ['this', 'signature'])
        self.system_requestable_slots = deepcopy(self.ontology.ontology['system_requestable'])

        self.dstc2_acts = None

        if not domain:
            # Default to CamRest dimensions
            self.NStateFeatures = 56

            # Default to CamRest actions
            self.dstc2_acts = ['repeat', 'canthelp', 'affirm', 'negate', 'deny', 'ack', 'thankyou', 'bye',
                               'reqmore', 'hello', 'welcomemsg', 'expl-conf', 'select', 'offer', 'reqalts',
                               'confirm-domain', 'confirm']
        else:
            # Try to identify number of state features
            if domain in ['SlotFilling', 'CamRest']:
                DState = SlotFillingDialogueState({'slots': self.informable_slots})

                # Sub-case for CamRest
                if domain == 'CamRest':
                    # Does not include inform and request that are modelled together with their arguments
                    self.dstc2_acts_sys = ['offer', 'canthelp', 'affirm', 'deny', 'ack', 'bye', 'reqmore', 'welcomemsg',
                                           'expl-conf', 'select', 'repeat', 'confirm-domain', 'confirm']

                    # Does not include inform and request that are modelled together with their arguments
                    self.dstc2_acts_usr = ['affirm', 'negate', 'deny', 'ack', 'thankyou', 'bye', 'reqmore', 'hello',
                                           'expl-conf', 'repeat', 'reqalts', 'restart', 'confirm']

                    if self.agent_role == 'system':
                        self.dstc2_acts = self.dstc2_acts_sys

                    elif self.agent_role == 'user':
                        self.dstc2_acts = self.dstc2_acts_usr

                    self.NActions = len(self.dstc2_acts) + len(self.requestable_slots)
                    self.NOtherActions = len(self.dstc2_acts) + len(self.requestable_slots)

                    if self.agent_role == 'system':
                        self.NActions += len(self.system_requestable_slots)
                        self.NOtherActions += len(self.requestable_slots)
                    else:
                        self.NActions += len(self.requestable_slots)
                        self.NOtherActions += len(self.system_requestable_slots)

            else:
                print('Warning! Domain has not been defined. Using Dummy Dialogue State')
                DState = DummyDialogueState({'slots': self.informable_slots})

                self.NActions = 5
                self.NOtherActions = 5

            DState.initialize()
            self.NStateFeatures = len(self.encode_state(DState))
            print('Supervised Policy automatically determined number of state features: {0}'.format(self.NStateFeatures))

        self.policy_alpha = 0.05

        self.tf_saver = None

    def initialize(self, **kwargs):
        if self.agent_role == 'system':
            self.warmup_policy = HandcraftedPolicy(self.ontology)

        elif self.agent_role == 'user':
            usim_args = dict(zip(['ontology', 'database'], [self.ontology, self.database]))
            self.warmup_simulator = AgendaBasedUS(usim_args)

        if 'is_training' in kwargs:
            self.is_training = bool(kwargs['is_training'])

            if self.agent_role == 'user' and self.warmup_simulator:
                if 'goal' in kwargs:
                    self.warmup_simulator.initialize({kwargs['goal']})
                else:
                    print('WARNING ! No goal provided for Supervised policy user simulator @ initialize')
                    self.warmup_simulator.initialize({})

        if 'policy_path' in kwargs:
            self.policy_path = kwargs['policy_path']

        if 'learning_rate' in kwargs:
            self.policy_alpha = kwargs['learning_rate']

        if self.sess is None:
            self.policy_net = self.feed_forward_net_init()
            self.sess = tf.InteractiveSession()
            self.sess.run(tf.global_variables_initializer())

            self.tf_saver = tf.train.Saver(var_list=tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES,
                                                                      scope=self.tf_scope))

    def restart(self, args):
        if self.agent_role == 'user' and self.warmup_simulator:
            if 'goal' in args:
                self.warmup_simulator.initialize(args)
            else:
                print('WARNING! No goal provided for Supervised policy user simulator @ restart')
                self.warmup_simulator.initialize({})

    def next_action(self, state):
        if self.is_training:
            # This is a Supervised Policy, so no exploration here.

            if self.agent_role == 'system':
                return self.warmup_policy.next_action(state)
            else:
                self.warmup_simulator.receive_input(state.user_acts, state.user_goal)
                return self.warmup_simulator.generate_output()

        pl_calculated, pl_state, pl_newvals, pl_optimizer, pl_loss = self.policy_net

        obs_vector = np.expand_dims(self.encode_state(state), axis=0)

        probs = self.sess.run(pl_calculated, feed_dict={pl_state: obs_vector})

        if self.IS_GREEDY_POLICY:
            # Greedy policy: Return action with maximum value from the given state

            if self.agent_role == 'user':
                threshold = 0.75
            else:
                threshold = 0.75

            predictions = [int(p > threshold) for p in probs[0]]
            sys_acts = self.decode_action(predictions, self.agent_role == 'system')

            # max_pi = max(probs[0])
            # maxima = [i for i, j in enumerate(probs[0]) if j == max_pi]
            #
            # # Break ties randomly
            # if maxima:
            #     sys_acts = self.decode_action([random.choice(maxima)], self.agent_role == 'system')
            # else:
            #     print(f'--- {self.agent_role}: Warning! No maximum value identified for policy. Selecting random action.')
            #     return self.decode_action([random.choice(range(0, self.NActions))], self.agent_role == 'system')

        else:
            # Stochastic policy: Sample action wrt Q values
            if any(np.isnan(probs[0])):
                print('WARNING! Supervised Policy: NAN detected in action probabilities! Selecting random action.')
                return self.decode_action(random.choice(range(0, self.NActions)), self.agent_role == 'system')

            # Make sure weights are positive
            min_p = min(probs[0])

            if min_p < 0:
                positive_weights = [p + abs(min_p) for p in probs[0]]
            else:
                positive_weights = probs[0]

            # Normalize weights
            positive_weights /= sum(positive_weights)

            sys_acts = self.decode_action(random.choices([a for a in range(self.NActions)], weights=positive_weights)[0],
                                          self.agent_role == 'system')

        # TODO Loop over all returned sys acts here
        # if sys_acts[0].intent == 'inform' and state.item_in_focus:
        #     dactitems = []
        #
        #     for item in state.item_in_focus:
        #         dactitems.append(DialogueActItem(item, Operator.EQ, state.item_in_focus[item]))

        return sys_acts

    def feed_forward_net_init(self):
        self.tf_scope = "policy_" + self.agent_role + '_' + str(self.agent_id)

        with tf.variable_scope(self.tf_scope):
            state = tf.placeholder("float", [None, self.NStateFeatures])
            newvals = tf.placeholder("float", [None, self.NActions])

            w1 = tf.get_variable("w1", [self.NStateFeatures, self.NStateFeatures])
            b1 = tf.get_variable("b1", [self.NStateFeatures])
            h1 = tf.nn.relu(tf.matmul(state, w1) + b1)
            # h1 = tf.nn.sigmoid(tf.matmul(state, w1) + b1)

            w2 = tf.get_variable("w2", [self.NStateFeatures, self.NStateFeatures])
            b2 = tf.get_variable("b2", [self.NStateFeatures])
            h2 = tf.nn.relu(tf.matmul(h1, w2) + b2)
            # h2 = tf.nn.sigmoid(tf.matmul(h1, w2) + b2)

            # w3 = tf.get_variable("w3", [self.NStateFeatures, self.NStateFeatures])
            # b3 = tf.get_variable("b3", [self.NStateFeatures])
            # # h3 = tf.nn.relu(tf.matmul(h2, w3) + b3)
            # h3 = tf.nn.softmax(tf.matmul(h2, w3) + b3)

            w3 = tf.get_variable("w3", [self.NStateFeatures, self.NActions])
            b3 = tf.get_variable("b3", [self.NActions])

            # calculated = tf.nn.softmax(tf.matmul(h2, w3) + b3)
            calculated = tf.nn.sigmoid(tf.matmul(h2, w3) + b3)

            diffs = calculated - newvals
            loss = tf.nn.l2_loss(diffs)
            optimizer = tf.train.AdamOptimizer(self.policy_alpha).minimize(loss)

            return calculated, state, newvals, optimizer, loss

    def train(self, dialogues):
        # If called by accident
        if not self.is_training:
            return

        pl_calculated, pl_state, pl_newvals, pl_optimizer, pl_loss = self.policy_net

        states = []
        actions = []

        for dialogue in dialogues:
            for index, turn in enumerate(dialogue):
                act_enc = self.encode_action(turn['action'], self.agent_role == 'system')
                if act_enc:
                    states.append(self.encode_state(turn['state']))
                    action = np.zeros(self.NActions)
                    for a in act_enc:
                        action[a] = 1

                    actions.append(action)

        # Train policy
        self.sess.run(pl_optimizer, feed_dict={pl_state: states, pl_newvals: actions})

    def encode_state_small(self, state):
        temp = []

        num_filled_slots = 0
        for value in state.slots_filled.values():
            if value:
                num_filled_slots += 1

        temp += [int(b) for b in format(num_filled_slots, '02b')]

        temp.append(int(state.is_terminal_state))

        if state.user_acts:
            temp += [int(b) for b in format(self.encode_action(state.user_acts,
                                                               system=self.agent_role != 'system'), '06b')]
        else:
            temp += [0] * 6

        # If the agent plays the role of the user it needs access to its own goal
        if self.agent_role == 'user':
            if state.user_goal:
                for c in self.informable_slots:
                    if c != 'name':
                        if c in state.user_goal.constraints:
                            temp.append(1)
                        else:
                            temp.append(0)

                        # if c in state.user_goal.actual_constraints:
                        #     temp.append(1)
                        # else:
                        #     temp.append(0)
            else:
                temp += [0] * (len(self.informable_slots)-1)

        return temp

    def encode_state(self, state):
        '''
        Encodes the dialogue state into an index used to address the Q matrix.

        :param state: the state to encode
        :return: int - a unique state encoding
        '''

        # return self.encode_state_small(state)

        temp = []

        temp += [int(b) for b in format(state.turn, '06b')]

        for value in state.slots_filled.values():
            temp.append(1) if value else temp.append(0)

        temp.append(int(state.is_terminal_state))

        # If the agent is a system, then this shows what the top db result is.
        # If the agent is a user, then this shows what information the system has provided
        # if self.agent_role == 'system':
        if state.item_in_focus:
            state_filled_info = []
            requested_slot = []

            for slot in self.requestable_slots:
                if slot in state.item_in_focus and state.item_in_focus[slot]:
                    state_filled_info.append(1)
                else:
                    state_filled_info.append(0)

                requested_slot.append(1) if slot == state.requested_slot else requested_slot.append(0)

            temp += state_filled_info + requested_slot

            if self.agent_role == 'system':
                if state.system_requestable_slot_entropies:
                    max_entr = max(state.system_requestable_slot_entropies.values())
                    temp += [1 if state.system_requestable_slot_entropies[s] == max_entr else 0 for s in
                             state.system_requestable_slot_entropies]
                else:
                    temp += [0] * len(self.system_requestable_slots)

        elif self.agent_role == 'system':
            temp += [0] * (2 * len(self.requestable_slots) + len(self.system_requestable_slots))

        elif self.agent_role == 'user':
            temp += [0] * (2 * len(self.requestable_slots))

        temp.append(1) if state.system_made_offer else temp.append(0)

        if state.user_acts:
            # If this agent is the system then "user" is a user (hopefully).
            # If this agent is a user then "user" is a system.
            act_enc = self.encode_action(state.user_acts, self.agent_role != 'system')
            uacts = [0] * self.NOtherActions

            for a in act_enc:
                uacts[a] = 1

            temp += uacts

            # if act_enc < 0:
            #     act_enc = 0
            #
            # temp += [int(b) for b in format(act_enc, '06b')]
        else:
            temp += [0] * self.NOtherActions

        if state.last_sys_acts:
            act_enc = self.encode_action([state.last_sys_acts[0]], self.agent_role == 'system')

            s_acts = [0] * self.NActions
            for a in act_enc:
                s_acts[a] = 1

            temp += s_acts

            # if not act_enc:
            #     act_enc = [0] * 6
            #
            # temp += [int(b) for b in format(act_enc, '06b')]
        else:
            # temp += [0] * 6
            temp += [0] * self.NActions

        # If the agent plays the role of the user it needs access to its own goal
        if state.user_goal:
            for c in self.informable_slots:
                if c != 'name':
                    if c in state.user_goal.constraints and state.user_goal.constraints[c].value:
                        temp.append(1)
                    else:
                        temp.append(0)

                    if c in state.user_goal.actual_constraints and state.user_goal.actual_constraints[c].value:
                        temp.append(1)
                    else:
                        temp.append(0)

            for r in self.requestable_slots:
                if r in state.user_goal.requests and state.user_goal.requests[r].value:
                    temp.append(1)
                else:
                    temp.append(0)

                if r in state.user_goal.actual_requests and state.user_goal.actual_requests[r].value:
                    temp.append(1)
                else:
                    temp.append(0)

        else:
            temp += [0] * (2 * (len(self.informable_slots)-1 + len(self.requestable_slots)))

        return temp

    def encode_action(self, actions, system=True):
        '''

        :param actions:
        :param system;
        :return:
        '''

        # TODO: Handle multiple actions
        # TODO: Action encoding in a principled way
        if not actions:
            print('WARNING: Supervised Policy action encoding called with empty actions list (returning -1).')
            return []

        enc_actions = []

        for action in actions:
            slot = None
            if action.params and action.params[0].slot:
                slot = action.params[0].slot

            if system:
                if self.dstc2_acts_sys and action.intent in self.dstc2_acts_sys:
                    enc_actions.append(self.dstc2_acts_sys.index(action.intent))

                elif slot:
                    if action.intent == 'request' and slot in self.system_requestable_slots:
                        enc_actions.append(len(self.dstc2_acts_sys) + self.system_requestable_slots.index(slot))

                    elif action.intent == 'inform' and slot in self.requestable_slots:
                        enc_actions.append(len(self.dstc2_acts_sys) + len(self.system_requestable_slots) + self.requestable_slots.index(slot))
            else:
                if self.dstc2_acts_usr and action.intent in self.dstc2_acts_usr:
                    enc_actions.append(self.dstc2_acts_usr.index(action.intent))

                elif slot:
                    if action.intent == 'request' and slot in self.requestable_slots:
                        enc_actions.append(len(self.dstc2_acts_usr) + self.requestable_slots.index(slot))

                    elif action.intent == 'inform' and slot in self.requestable_slots:
                        enc_actions.append(len(self.dstc2_acts_usr) + len(self.requestable_slots) + self.requestable_slots.index(slot))

        # Default fall-back action
        if not enc_actions:
            print('Supervised ({0}) policy action encoder warning: Selecting default action (unable to encode: {1})!'.format(self.agent_role, actions[0]))

        return enc_actions

    def decode_action(self, action_enc_vec, system=True):
        '''

        :param action_enc:
        :param system:
        :return:
        '''
        dacts = []

        for a in range(len(action_enc_vec)):
            # Skip inactive actions
            if action_enc_vec[a] == 1:
                action_enc = a

                if system:
                    if action_enc < len(self.dstc2_acts_sys):
                        dacts += [DialogueAct(self.dstc2_acts_sys[action_enc], [])]

                    elif action_enc < len(self.dstc2_acts_sys) + len(self.system_requestable_slots):
                        dacts += [DialogueAct('request',
                                            [DialogueActItem(
                                                self.system_requestable_slots[action_enc - len(self.dstc2_acts_sys)],
                                                Operator.EQ, '')])]

                    elif action_enc < len(self.dstc2_acts_sys) + len(self.system_requestable_slots) + len(self.requestable_slots):
                        index = action_enc - len(self.dstc2_acts_sys) - len(self.system_requestable_slots)
                        dacts += [DialogueAct('inform', [DialogueActItem(self.requestable_slots[index], Operator.EQ, '')])]

                else:
                    if action_enc < len(self.dstc2_acts_usr):
                        dacts += [DialogueAct(self.dstc2_acts_usr[action_enc], [])]

                    elif action_enc < len(self.dstc2_acts_usr) + len(self.requestable_slots):
                        dacts += [DialogueAct('request',
                                            [DialogueActItem(self.requestable_slots[action_enc - len(self.dstc2_acts_usr)],
                                                             Operator.EQ, '')])]

                    elif action_enc < len(self.dstc2_acts_usr) + 2 * len(self.requestable_slots):
                        dacts += [DialogueAct('inform',
                                            [DialogueActItem(self.requestable_slots[action_enc - len(self.dstc2_acts_usr) - len(
                                                self.requestable_slots)], Operator.EQ, '')])]

        return dacts

    def save(self, path=None):
        # Don't save if not training
        if not self.is_training:
            return

        print('DEBUG: {0} learning rate is: {1}'.format(self.agent_role, self.policy_alpha))

        pol_path = path

        if not pol_path:
            pol_path = self.policy_path

        if not pol_path:
            pol_path = 'Models/Policies/supervised_policy_' + self.agent_role + '_' + str(self.agent_id)

        if self.sess is not None and self.is_training:
            save_path = self.tf_saver.save(self.sess, pol_path)
            print('Supervised Policy model saved at: %s' % save_path)

    def load(self, path=None):
        pol_path = path

        if not pol_path:
            pol_path = self.policy_path

        if not pol_path:
            pol_path = 'Models/Policies/supervised_policy_' + self.agent_role + '_' + str(self.agent_id)

        if os.path.isfile(pol_path + '.meta'):
            self.policy_net = self.feed_forward_net_init()
            self.sess = tf.InteractiveSession()

            self.tf_saver = tf.train.Saver(var_list=tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES,
                                                                      scope=self.tf_scope))

            self.tf_saver.restore(self.sess, pol_path)

            print('Supervised Policy model loaded from {0}.'.format(pol_path))


