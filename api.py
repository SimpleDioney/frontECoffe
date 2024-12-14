from fastapi import FastAPI
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
import requests
import time
import logging
import asyncio
import os
import json
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from datetime import datetime
import urllib.parse
from dotenv import load_dotenv
from uber_rides.session import Session
from uber_rides.client import UberRidesClient


# Carrega variáveis de ambiente
load_dotenv()

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Bot de Delivery")

class DeliveryCalculator:
    def __init__(self):
        # Credenciais OAuth
        self.client_id = "tos4VMUfM48AflTWRcrBMLL8gkO-3HJs"
        self.client_secret = "W_VfkbMMaqvVvLpUUBy7NBCqB1sZDX0nevgVdX0c"
        self.uber_auth_url = "https://login.uber.com/oauth/v2/token"
        self.uber_api_url = "https://api.uber.com/v1/delivery"

        # Token e sua validade
        self._access_token = None
        self._token_expiry = None

        # Coordenadas e dados do restaurante
        self.restaurant_coords = (-23.333983, -51.150636)
        self.restaurant_name = "Restaurante"
        self.restaurant_phone = "+5543999999999"

        # Configurações de preço para fallback
        self.min_delivery_fee = 8.00
        self.base_time = 30
        self.rush_hour_fee = 5.00

        # Geocoder
        self.geocoder = Nominatim(
            user_agent="delivery_bot_1.0",
            timeout=10
        )

        # Cache de geocodificação
        self.address_cache = {}

    def _get_access_token(self) -> Optional[str]:
        """Obtém ou renova o token de acesso"""
        try:
            # Se o token ainda é válido, retorna ele
            if (self._access_token and self._token_expiry 
                and datetime.now() < self._token_expiry):
                return self._access_token

            # Caso contrário, solicita um novo
            payload = {
    'client_id': self.client_id,
    'client_secret': self.client_secret,
    'grant_type': 'client_credentials',
    'scope': 'delivery_quotes delivery_write'
}


            response = requests.post(
                self.uber_auth_url,
                data=payload
            )

            if response.status_code == 200:
                data = response.json()
                self._access_token = data['access_token']
                # Define validade para 1 hora antes do tempo real para margem
                self._token_expiry = datetime.now() + timedelta(
                    seconds=data['expires_in'] - 3600
                )
                return self._access_token
            else:
                logging.error(
                    f"Erro ao obter token: {response.status_code} - {response.text}"
                )
                return None

        except Exception as e:
            logging.error(f"Erro na autenticação: {str(e)}")
            return None

    def get_coordinates(self, address: str) -> Optional[Tuple[float, float]]:
        """Obtém coordenadas do endereço"""
        try:
            if address in self.address_cache:
                return self.address_cache[address]

            if 'londrina' not in address.lower():
                search_address = f"{address}, Londrina, PR, Brasil"
            else:
                search_address = address

            location = self.geocoder.geocode(search_address)

            if location:
                coords = (location.latitude, location.longitude)
                self.address_cache[address] = coords
                return coords

            return None

        except Exception as e:
            logging.error(f"Erro ao geocodificar endereço: {str(e)}")
            return None

    def get_uber_estimate(self, dest_coords: Tuple[float, float]) -> Optional[Dict]:
        """Obtém estimativa da Uber Flash Moto"""
        try:
            # Obtém token válido
            token = self._get_access_token()
            if not token:
                return None

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept-Language": "pt_BR"
            }

            payload = {
                "pickup_address": {
                    "lat": self.restaurant_coords[0],
                    "lon": self.restaurant_coords[1],
                    "address": "Endereço do Restaurante",
                    "contact": {
                        "name": self.restaurant_name,
                        "phone_number": self.restaurant_phone.replace("+", "")
                    }
                },
                "dropoff_address": {
                    "lat": dest_coords[0],
                    "lon": dest_coords[1],
                    "contact": {
                        "name": "Cliente",
                        "phone_number": ""
                    }
                },
                "manifest": {
                    "reference": "delivery_estimate",
                    "items": [
                        {
                            "name": "Pedido",
                            "quantity": 1,
                            "size": "medium"
                        }
                    ]
                },
                "delivery_service_class": "flash_moto"
            }

            response = requests.post(
                f"{self.uber_api_url}/quotes",
                headers=headers,
                json=payload
            )

            if response.status_code == 200:
                result = response.json()
                logging.info(f"Estimativa Uber obtida com sucesso: {result}")

                # Pega a primeira cotação (geralmente a mais barata)
                quote = result["quotes"][0]

                return {
                    "fee": float(quote["fee"]["total"]),
                    "time": int(quote["pickup_duration"] + quote["dropoff_duration"]),
                    "surge": quote.get("surge_multiplier", 1.0),
                    "source": "uber"
                }
            else:
                logging.error(f"Erro na API Uber: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logging.error(f"Erro ao obter estimativa da Uber: {str(e)}")
            return None

    def calculate_delivery(self, address: str) -> Dict:
        """Calcula preço e tempo de entrega"""
        try:
            coords = self.get_coordinates(address)

            if coords:
                uber_estimate = self.get_uber_estimate(coords)

                if uber_estimate:
                    return {
                        "fee": uber_estimate["fee"],
                        "time": uber_estimate["time"],
                        "coordinates": coords,
                        "rush_hour": uber_estimate["surge"] > 1.0,
                        "source": "uber"
                    }

            return self.get_fallback_estimate()

        except Exception as e:
            logging.error(f"Erro ao calcular entrega: {str(e)}")
            return self.get_fallback_estimate()

    def get_fallback_estimate(self) -> Dict:
        """Sistema de fallback para quando a API falha"""
        fee = self.min_delivery_fee

        if self.is_rush_hour():
            fee += self.rush_hour_fee

        return {
            "fee": round(fee, 2),
            "time": self.base_time,
            "coordinates": None,
            "rush_hour": self.is_rush_hour(),
            "source": "fallback"
        }

    def is_rush_hour(self) -> bool:
        """Verifica se é horário de pico"""
        current_hour = datetime.now().hour
        return (11 <= current_hour <= 14) or (18 <= current_hour <= 21)

class WhatsAppBot:
    def __init__(self):
        try:
            # Configura o diretório do perfil do Chrome
            current_dir = Path.cwd()
            self.profile_dir = current_dir / "whatsapp_profile"
            self.profile_dir.mkdir(exist_ok=True)
            
            logger.info(f"Usando perfil em: {str(self.profile_dir)}")
            
            # Configuração do Chrome
            chrome_options = Options()
            chrome_options.add_argument(f"--user-data-dir={str(self.profile_dir)}")
            chrome_options.add_argument("--profile-directory=Default")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--start-maximized")
            chrome_options.add_argument("--disable-notifications")
            
            # Inicializa o driver
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            
            self.wait = WebDriverWait(self.driver, 60)
            self.short_wait = WebDriverWait(self.driver, 5)
            self.is_ready = False
            
            # Inicializa calculadora de entrega
            self.delivery_calculator = DeliveryCalculator()
            
            logger.info("Bot inicializado com sucesso")
            
        except Exception as e:
            logger.error(f"Erro ao inicializar driver: {str(e)}")
            raise

    def start(self):
        """Inicia o WhatsApp Web"""
        try:
            self.driver.get("https://web.whatsapp.com")
            logger.info("WhatsApp Web aberto. Aguardando conexão...")
            
            try:
                self.wait.until(
                    EC.presence_of_element_located((By.CLASS_NAME, "x1y332i5"))
                )
                logger.info("Sessão anterior encontrada!")
                self.is_ready = True
                return True
            except:
                logger.info("Sessão anterior não encontrada, aguardando QR code...")
                
                self.wait.until(
                    EC.presence_of_element_located((By.CLASS_NAME, "x1y332i5"))
                )
                self.is_ready = True
                logger.info("WhatsApp Web conectado!")
                return True
            
        except Exception as e:
            logger.error(f"Erro ao iniciar WhatsApp: {str(e)}")
            return False

    def check_new_messages(self):
        """Verifica e processa novas mensagens"""
        try:
            unread_chats = self.driver.find_elements(
                By.CSS_SELECTOR, 
                'div._ahlk span[aria-label*="mensagem não lida"]'
            )
            
            logger.info(f"Encontrados {len(unread_chats)} chats não lidos")
            
            for unread in unread_chats:
                try:
                    # Encontra e clica no chat
                    chat_div = unread.find_element(
                        By.XPATH, 
                        ".//ancestor::div[contains(@class, 'x10l6tqk')]"
                    )
                    chat_div.click()
                    time.sleep(1)
                    
                    # Pega a última mensagem
                    messages = self.driver.find_elements(
                        By.CSS_SELECTOR,
                        'div._akbu span[dir="ltr"]'
                    )
                    
                    if messages:
                        last_message = messages[-1].text.strip()
                        logger.info(f"Última mensagem: {last_message}")
                        
                        # Processa a mensagem e obtém resposta
                        response = self.process_message(last_message)
                        
                        if response:
                            logger.info("Enviando resposta")
                            success = self.send_message(response)
                            if success:
                                logger.info("Resposta enviada com sucesso")
                            else:
                                logger.error("Falha ao enviar resposta")
                
                except Exception as e:
                    logger.error(f"Erro ao processar chat não lido: {str(e)}")
                    continue
                
                finally:
                    # Sempre tenta voltar para a tela inicial
                    try:
                        self.driver.refresh()
                        self.wait.until(
                            EC.presence_of_element_located((By.CLASS_NAME, "x1y332i5"))
                        )
                        time.sleep(1)
                    except:
                        pass
                    
        except Exception as e:
            logger.error(f"Erro ao verificar novas mensagens: {str(e)}")

    def process_message(self, message: str):
        """Processa mensagem recebida e define resposta apropriada"""
        try:
            message = message.lower().strip()
            
            if message == "pedido":
                return ("Olá! Por favor, envie seu endereço completo para "
                       "calcularmos o valor da entrega.")
            
            if message in ["confirmar", "confirmado"]:
                return (
                    " Pedido confirmado!\n\n"
                    "Em breve iniciaremos a preparação.\n"
                    "Você receberá atualizações sobre o status do seu pedido."
                )
                
            if message in ["cancelar", "cancela"]:
                return " Pedido cancelado. Obrigado pela preferência!"
            
            # Tenta calcular entrega para qualquer outra mensagem
            delivery_info = self.delivery_calculator.calculate_delivery(message)
            if delivery_info:
                return (
                    f" Endereço recebido!\n\n"
                    f"Taxa de entrega: R$ {delivery_info['fee']:.2f}\n"
                    f"Tempo estimado: {delivery_info['time']} minutos\n\n"
                    f"Digite 'confirmar' para prosseguir ou 'cancelar' para cancelar o pedido."
                )
            
            return None
            
        except Exception as e:
            logger.error(f"Erro ao processar mensagem: {str(e)}")
            return None

    def send_message(self, message: str):
        """Envia mensagem usando o chat atual"""
        try:
            # Localiza o campo de texto
            input_box = self.wait.until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR, 
                    'div[contenteditable="true"][data-tab="10"]'
                ))
            )
            
            # Limpa qualquer texto existente
            input_box.clear()
            
            # Envia caractere por caractere para garantir que o texto seja inserido
            for char in message:
                input_box.send_keys(char)
                time.sleep(0.01)  # Pequena pausa entre caracteres
            
            time.sleep(0.5)  # Aguarda um pouco antes de enviar
            
            # Tenta diferentes formas de enviar a mensagem
            try:
                # Primeiro tenta usando Enter
                input_box.send_keys(Keys.ENTER)
            except:
                try:
                    # Se falhar, tenta encontrar e clicar no botão de enviar
                    send_button = self.driver.find_element(
                        By.CSS_SELECTOR,
                        'button[data-testid="compose-btn-send"]'
                    )
                    send_button.click()
                except:
                    # Se ainda falhar, tenta usando JavaScript
                    self.driver.execute_script(
                        'document.querySelector(\'button[data-testid="compose-btn-send"]\').click();'
                    )
            
            time.sleep(1)  # Aguarda a mensagem ser enviada
            
            # Atualiza a página para fechar o chat
            self.driver.refresh()
            
            # Aguarda a página carregar
            self.wait.until(
                EC.presence_of_element_located((By.CLASS_NAME, "x1y332i5"))
            )
            logger.info("Mensagem enviada com sucesso")
            time.sleep(1)
            
            return True
            
        except Exception as e:
            logger.error(f"Erro ao enviar mensagem: {str(e)}")
            try:
                self.driver.refresh()
                self.wait.until(
                    EC.presence_of_element_located((By.CLASS_NAME, "x1y332i5"))
                )
            except:
                pass
            return False

    def quit(self):
        """Fecha o navegador de forma segura"""
        try:
            if self.driver:
                self.driver.quit()
                logger.info("Driver fechado com sucesso")
        except Exception as e:
            logger.error(f"Erro ao fechar driver: {str(e)}")

# Instância global do bot
bot = None

async def check_messages_loop():
    """Loop contínuo para verificar novas mensagens"""
    while True:
        try:
            if bot and bot.is_ready:
                logger.info("Verificando novas mensagens...")
                bot.check_new_messages()
            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"Erro no loop de verificação: {str(e)}")
            await asyncio.sleep(5)

@app.on_event("startup")
@app.on_event("startup")
async def startup_event():
    """Inicia o bot quando a API iniciar"""
    global bot
    try:
        bot = WhatsAppBot()
        success = bot.start()
        if success:
            logger.info("Bot iniciado com sucesso!")
            asyncio.create_task(check_messages_loop())
        else:
            logger.error("Falha ao iniciar bot")
    except Exception as e:
        logger.error(f"Erro durante inicialização: {str(e)}")

@app.on_event("shutdown")
async def shutdown_event():
    """Finaliza o bot quando a API for encerrada"""
    global bot
    if bot:
        bot.quit()

@app.get("/status")
def get_status():
    """Endpoint para verificar status do bot"""
    global bot
    if not bot:
        return {"status": "not_initialized"}
    
    return {
        "status": "connected" if bot.is_ready else "disconnected",
        "coordinates": bot.delivery_calculator.restaurant_coords,
        "rush_hour": bot.delivery_calculator.is_rush_hour()
    }

@app.get("/test_delivery/{address}")
def test_delivery(address: str):
    """Endpoint para testar cálculo de entrega"""
    global bot
    if not bot:
        return {"error": "Bot não inicializado"}
    
    try:
        delivery_info = bot.delivery_calculator.calculate_delivery(address)
        return {
            "address": address,
            "delivery_info": delivery_info
        }
    except Exception as e:
        return {"error": str(e)}

@app.post("/send_message")
async def send_message(data: dict):
    """Endpoint para enviar mensagem manual"""
    global bot
    if not bot:
        return {"error": "Bot não inicializado"}
    
    try:
        phone = data.get("phone")
        message = data.get("message")
        if not phone or not message:
            return {"error": "Phone e message são obrigatórios"}
            
        # Envia mensagem
        success = bot.send_message(message)
        return {"success": success}
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5434)