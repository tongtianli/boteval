

from pathlib import Path
import json
from threading import Thread
from typing import List, Mapping, Optional, Union
import functools
from datetime import datetime
import copy

from sqlalchemy import func
from sqlalchemy.orm import attributes

from  . import log, db
from .model import ChatTopic, User, ChatMessage, ChatThread, UserThread
from .bots import BotAgent, load_bot_agent
from .utils import jsonify
from . import config


class ChatManager:

    def __init__(self, thread: ChatThread) -> None:
        self.thread: ChatThread = thread

    def new_message(self, message):
        raise NotImplementedError()


class DialogBotChatManager(ChatManager):

    # 1-on-1 dialog between human and a bot

    def __init__(self, thread: ChatThread, bot_agent:BotAgent,
                 max_turns:int=config.DEF_MAX_TURNS_PER_THREAD):
        super().__init__(thread)
        bots = [ user for user in thread.users if user.role == User.ROLE_BOT ]
        user_ids = [u.id for u in thread.users]
        assert len(bots) == 1, f'Expect 1 bot in thead {thread.id}; found {len(bots)}; Users: {user_ids}'
        self.bot_user = bots[0]
        assert bot_agent
        self.bot_agent = bot_agent

        humans = [ user for user in thread.users if user.role == User.ROLE_HUMAN ]
        assert len(humans) == 1, f'Expect 1 human in thead {thread.id}; found {len(humans)}; Users: {user_ids}'
        self.human_user = humans[0]

        self.max_turns = max_turns
        self.num_turns = thread.count_turns(self.human_user)


    def observe_message(self, message: ChatMessage) -> ChatMessage:
        # Observe and reply
        # this message is from human
        assert message.user_id == self.human_user.id
        self.thread.messages.append(message)
        reply = self.bot_reply()
        self.thread.messages.append(reply)
        db.session.add(message)
        db.session.add(reply)
        db.session.commit()
        self.num_turns += 1
        episode_done = self.num_turns >= self.max_turns
        log.info(f'{self.thread.id} turns:{self.num_turns} max:{self.max_turns}')
        return reply, episode_done

    def bot_reply(self) -> ChatMessage:
        # only using last message as context
        text = self.thread.messages[-1].text if self.thread.messages else ''
        reply = self.bot_agent.talk(text)
        msg = ChatMessage(user_id = self.bot_user.id, text=reply, thread_id = self.thread.id)
        return msg


class FileExportService:
    """Export chat data from database into file system"""

    def __init__(self, data_dir: Union[Path,str]) -> None:
        if not isinstance(data_dir, Path):
            data_dir = Path(data_dir)
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def export_thread(self, thread: ChatThread, **meta):
        dt_now = datetime.now()
        file_name = dt_now.strftime("%Y%m%d-%H%M%S") + f'-{thread.topic_id}_{thread.id}.json'
        path = self.data_dir / dt_now.strftime("%Y%m%d") / file_name
        log.info(f'Export thread {thread.id} to {path}')
        path.parent.mkdir(exist_ok=True, parents=True)
        with open(path, 'w', encoding='utf-8') as out:
            data = thread.as_dict()
            data['_exported_'] = str(dt_now)
            data['meta'] = meta
            json.dump(data, out, indent=2, ensure_ascii=False)


class ChatService:

    def __init__(self, config):
        self.config = config
        self._bot_user = None
        self._context_user = None
        self.topics_file = self.config['chatbot']['topics_file']

        self.exporter = FileExportService(config['chat_dir'])
        bot_name = config['chatbot']['bot_name']
        bot_args = config['chatbot'].get('bot_args') or {}
        self.bot_agent = load_bot_agent(bot_name, bot_args)
        self.limits = config.get('limits') or {}
        self.ratings = config['ratings']

        self.onboarding = config.get('onboarding') and copy.deepcopy(config['onboarding'])
        if  self.onboarding and 'agreement_file' in self.onboarding:
            self.onboarding['agreement_text'] = Path(self.onboarding['agreement_file']).read_text()

    @property
    def bot_user(self):
        if not self._bot_user:
            self._bot_user = User.query.get(config.Auth.BOT_USER)
        return self._bot_user

    @property
    def context_user(self):
        if not self._context_user:
            self._context_user = User.query.get(config.Auth.CONTEXT_USER)
        return self._context_user


    def init_db(self, init_topics=True):

        if not User.query.get(config.Auth.ADMIN_USER):
            User.create_new(
                id=config.Auth.ADMIN_USER, name='Chat Admin',
                secret=config.Auth.ADMIN_SECRET, role=User.ROLE_ADMIN)

        if not User.query.get(config.Auth.DEV_USER): # for development
            User.create_new(id=config.Auth.DEV_USER, name='Developer',
                            secret=config.Auth.DEV_SECRET,
                            role=User.ROLE_HUMAN)

        if not User.query.get(config.Auth.BOT_USER):
            # login not enabled. directly insert with empty string as secret
            db.session.add(User(id=config.Auth.BOT_USER, name='Chat Bot',
                                secret='', role=User.ROLE_BOT))

        if not User.query.get(config.Auth.CONTEXT_USER):
            # for loading context messages
            db.session.add(User(id=config.Auth.CONTEXT_USER,
                                name='Context User', secret='',
                                role=User.ROLE_HIDDEN))


        if init_topics:
            assert self.topics_file
            topics_file = Path(self.topics_file).resolve()
            topics_file.exists(), f'{topics_file} not found'

            with open(topics_file, encoding='utf-8') as out:
                topics = json.load(out)
            assert isinstance(topics, list)
            log.info(f'found {len(topics)} topics in {topics_file}')
            objs = []
            for topic in topics:
                obj = ChatTopic.query.get(topic['id'])
                if obj:
                    log.warning(f'Chat topic exisits {topic["id"]}, so skipping')
                    continue
                obj = ChatTopic(id=topic['id'], name=topic['name'], data=topic)
                objs.append(obj)
            if objs:
                log.info(f"Inserting {len(objs)} topics to db")
                db.session.add_all(objs)
        db.session.commit()

    def get_topics(self):
        return ChatTopic.query.all()

    def get_user_threads(self, user):
        return ChatThread.query.join(User, ChatThread.users).filter(User.id==user.id).all()

    def get_topic(self, topic_id):
        return ChatTopic.query.get(topic_id)

    def get_thread_for_topic(self, user, topic, create_if_missing=True) -> Optional[ChatThread]:
        topic_threads = ChatThread.query.filter_by(topic_id=topic.id).all()
        # TODO: appply this second filter directly into sqlalchemy
        thread = None
        for tt in topic_threads:
            if any(user.id == tu.id for tu in tt.users):
                log.info('Topic thread alredy exists; reusing it')
                thread = tt

        if not thread and create_if_missing:
            log.info(f'creating a thread: user: {user.id} topic: {topic.id}')
            thread = ChatThread(topic_id=topic.id)
            thread.users.append(user)
            thread.users.append(self.bot_user)
            thread.users.append(self.context_user)
            db.session.add(thread)
            db.session.flush()  # flush it to get thread_id
            for m in topic.data['conversation']:
                text = m['text']
                data =  dict(text_orig=m.get('text_orig'), speaker_id= m.get('speaker_id'), fake_start=True)
                msg = ChatMessage(text=text, user_id=self.context_user.id, thread_id=thread.id, data=data)
                db.session.add(msg)
                thread.messages.append(msg)
            db.session.merge(thread)
            db.session.flush()
            db.session.commit()
        return thread

    def get_thread(self, thread_id) -> Optional[ChatThread]:
        return ChatThread.query.get(thread_id)

    def get_threads(self, user: User) -> List[ChatThread]:
        log.info(f'Querying {user.id}')
        threads = UserThread.query.filter(user_id=user.id).all()
        #threads = ChatThread.query.filter_by(user_id=user.id).all()
        log.info(f'Found {len(threads)} threads found')
        return threads

    def get_thread_counts(self, episode_done=True) -> Mapping[str, int]:
        thread_counts = ChatThread.query.filter_by(episode_done=bool(episode_done))\
            .with_entities(ChatThread.topic_id, func.count(ChatThread.topic_id))\
            .group_by(ChatThread.topic_id).all()

        return {tid: count for tid, count in thread_counts }

    def update_thread_ratings(self, thread: ChatThread, ratings:dict):
        thread.data.update(dict(ratings=ratings, rating_done=True))
        thread.episode_done = True
        # sometimes JSON field updates are not automatically detected.
        # https://github.com/sqlalchemy/sqlalchemy/discussions/6473
        attributes.flag_modified(thread, 'data')
        db.session.merge(thread)
        db.session.flush()
        db.session.commit()
        self.exporter.export_thread(thread, rating_questions=self.ratings)



    @functools.lru_cache(maxsize=256)
    def cached_get(self, thread):
        max_turns = self.config.get('limits', {}).get('max_turns_per_thread',
                                                      config.DEF_MAX_TURNS_PER_THREAD)
        return DialogBotChatManager(thread=thread, bot_agent=self.bot_agent,
                                    max_turns=max_turns)

    def new_message(self, msg: ChatMessage, thread: ChatThread) -> ChatMessage:
        dialog = self.cached_get(thread)
        reply, episode_done = dialog.observe_message(msg)
        return reply, episode_done

    def get_rating_questions(self):
        return self.ratings
