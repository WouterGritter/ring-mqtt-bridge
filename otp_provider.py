from typing import Awaitable, Callable

from flask import Flask, request, send_from_directory
import asyncio
from hypercorn.asyncio import serve
from hypercorn.config import Config


class OtpProvider:
    def __init__(self, listen_port: int):
        self.listen_port = listen_port

        self.app = Flask(__name__)
        self.otp_future = None

        @self.app.route('/')
        def index():
            return send_from_directory(self.app.static_folder, 'index.html')

        @self.app.route('/provide-otp', methods=['POST'])
        def provide_otp():
            otp = request.json.get('otp') if request.is_json else request.form.get('otp')
            if otp and self.otp_future:
                self.otp_future.set_result(otp)
                self.otp_future = None
                return 'OTP received', 200
            return 'No OTP provided or no listener awaiting', 400

    async def start_server(self):
        config = Config()
        config.bind = [f'0.0.0.0:{self.listen_port}']
        await serve(self.app, config)

    async def provide_otp(self) -> str:
        self.otp_future = asyncio.get_event_loop().create_future()
        otp = await self.otp_future
        return otp
