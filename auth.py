# auth.py
from flask import Blueprint, redirect, url_for

auth = Blueprint('auth', __name__)

@auth.route('/login/google')
def login_google():
    # por enquanto só redireciona para o login normal
    return redirect(url_for('login'))

