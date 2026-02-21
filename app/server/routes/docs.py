from flask import Blueprint, jsonify, request, Response


docs_bp = Blueprint('docs', __name__)


def _openapi_spec(base_url: str):
    return {
        'openapi': '3.0.3',
        'info': {
            'title': 'Multiplayer Game Backend API',
            'version': '1.0.0',
            'description': 'API documentation for auth, parent, gameplay, server, and teacher dashboard endpoints.',
        },
        'servers': [{'url': base_url.rstrip('/')}],
        'components': {
            'securitySchemes': {
                'BearerAuth': {
                    'type': 'http',
                    'scheme': 'bearer',
                    'bearerFormat': 'JWT',
                    'description': 'Provide JWT token as: Bearer <token>',
                }
            },
            'schemas': {
                'ErrorResponse': {
                    'type': 'object',
                    'properties': {'error': {'type': 'string'}},
                },
                'MessageResponse': {
                    'type': 'object',
                    'properties': {'message': {'type': 'string'}},
                },
            },
        },
        'paths': {
            '/auth/register': {
                'post': {
                    'tags': ['Auth'],
                    'summary': 'Register a new user',
                    'requestBody': {
                        'required': True,
                        'content': {
                            'application/json': {
                                'schema': {
                                    'type': 'object',
                                    'required': ['username', 'email', 'password'],
                                    'properties': {
                                        'username': {'type': 'string'},
                                        'email': {'type': 'string', 'format': 'email'},
                                        'password': {'type': 'string'},
                                        'role': {'type': 'string', 'example': 'Student'},
                                    },
                                }
                            }
                        },
                    },
                    'responses': {
                        '201': {'description': 'User registered successfully'},
                        '400': {'description': 'Validation error'},
                    },
                }
            },
            '/auth/login': {
                'post': {
                    'tags': ['Auth'],
                    'summary': 'Login and get JWT token',
                    'requestBody': {
                        'required': True,
                        'content': {
                            'application/json': {
                                'schema': {
                                    'type': 'object',
                                    'required': ['username', 'password'],
                                    'properties': {
                                        'username': {'type': 'string'},
                                        'password': {'type': 'string'},
                                    },
                                }
                            }
                        },
                    },
                    'responses': {
                        '200': {'description': 'Returns access token'},
                        '401': {'description': 'Invalid credentials'},
                    },
                }
            },
            '/parent/link_child': {
                'post': {
                    'tags': ['Parent'],
                    'summary': 'Link a child account to parent',
                    'security': [{'BearerAuth': []}],
                    'requestBody': {
                        'required': True,
                        'content': {
                            'application/json': {
                                'schema': {
                                    'type': 'object',
                                    'required': ['child_username'],
                                    'properties': {'child_username': {'type': 'string'}},
                                }
                            }
                        },
                    },
                    'responses': {'200': {'description': 'Linked'}, '403': {'description': 'Unauthorized'}},
                }
            },
            '/parent/stats': {
                'get': {
                    'tags': ['Parent'],
                    'summary': 'Get children stats',
                    'security': [{'BearerAuth': []}],
                    'responses': {'200': {'description': 'Children stats'}, '403': {'description': 'Unauthorized'}},
                }
            },
            '/parent/unlink_child': {
                'post': {
                    'tags': ['Parent'],
                    'summary': 'Unlink a child account from parent',
                    'security': [{'BearerAuth': []}],
                    'requestBody': {
                        'required': True,
                        'content': {
                            'application/json': {
                                'schema': {
                                    'type': 'object',
                                    'required': ['child_username'],
                                    'properties': {'child_username': {'type': 'string'}},
                                }
                            }
                        },
                    },
                    'responses': {'200': {'description': 'Unlinked'}, '403': {'description': 'Unauthorized'}},
                }
            },
            '/server/register': {
                'post': {
                    'tags': ['Server Registry'],
                    'summary': 'Register or heartbeat a game server',
                    'requestBody': {
                        'required': True,
                        'content': {
                            'application/json': {
                                'schema': {
                                    'type': 'object',
                                    'required': ['port'],
                                    'properties': {
                                        'port': {'type': 'integer'},
                                        'name': {'type': 'string'},
                                        'count': {'type': 'integer'},
                                    },
                                }
                            }
                        },
                    },
                    'responses': {'200': {'description': 'OK'}},
                }
            },
            '/server/list': {
                'get': {
                    'tags': ['Server Registry'],
                    'summary': 'List active game servers',
                    'responses': {'200': {'description': 'List of active servers'}},
                }
            },
            '/mission/update': {
                'post': {
                    'tags': ['Gameplay'],
                    'summary': 'Create or update mission progress',
                    'security': [{'BearerAuth': []}],
                    'requestBody': {
                        'required': True,
                        'content': {
                            'application/json': {
                                'schema': {
                                    'type': 'object',
                                    'required': ['mission_public_id', 'score'],
                                    'properties': {
                                        'mission_public_id': {'type': 'string', 'format': 'uuid'},
                                        'score': {'type': 'integer'},
                                        'status': {'type': 'string', 'example': 'completed'},
                                    },
                                }
                            }
                        },
                    },
                    'responses': {
                        '200': {'description': 'Progress saved'},
                        '400': {'description': 'Invalid mission public ID or payload'},
                        '401': {'description': 'Missing or invalid token'},
                    },
                }
            },
            '/teacher/class/overview': {
                'get': {
                    'tags': ['Teacher'],
                    'summary': 'Get class overview with mission and quiz aggregates',
                    'security': [{'BearerAuth': []}],
                    'responses': {'200': {'description': 'Class overview'}, '403': {'description': 'Unauthorized'}},
                }
            },
            '/teacher/student/{student_public_id}': {
                'get': {
                    'tags': ['Teacher'],
                    'summary': 'Get detailed student performance summary',
                    'security': [{'BearerAuth': []}],
                    'parameters': [
                        {
                            'name': 'student_public_id',
                            'in': 'path',
                            'required': True,
                            'schema': {'type': 'string', 'format': 'uuid'},
                        }
                    ],
                    'responses': {
                        '200': {'description': 'Student summary'},
                        '403': {'description': 'Unauthorized'},
                        '404': {'description': 'Student not found'},
                    },
                }
            },
            '/teacher/quiz': {
                'post': {
                    'tags': ['Teacher'],
                    'summary': 'Create and schedule a quiz',
                    'security': [{'BearerAuth': []}],
                    'requestBody': {
                        'required': True,
                        'content': {
                            'application/json': {
                                'schema': {
                                    'type': 'object',
                                    'required': ['title'],
                                    'properties': {
                                        'title': {'type': 'string'},
                                        'timer_seconds': {'type': 'integer', 'default': 300},
                                        'start_date': {'type': 'string', 'format': 'date-time'},
                                    },
                                }
                            }
                        },
                    },
                    'responses': {'201': {'description': 'Quiz created'}, '400': {'description': 'Validation error'}},
                }
            },
            '/teacher/message': {
                'post': {
                    'tags': ['Teacher'],
                    'summary': 'Send feedback/message to student or parent',
                    'security': [{'BearerAuth': []}],
                    'requestBody': {
                        'required': True,
                        'content': {
                            'application/json': {
                                'schema': {
                                    'type': 'object',
                                    'required': ['receiver_public_id', 'content'],
                                    'properties': {
                                        'receiver_public_id': {'type': 'string', 'format': 'uuid'},
                                        'content': {'type': 'string'},
                                    },
                                }
                            }
                        },
                    },
                    'responses': {'201': {'description': 'Message sent'}, '403': {'description': 'Unauthorized'}},
                }
            },
            '/teacher/lobby/create': {
                'post': {
                    'tags': ['Teacher'],
                    'summary': 'Create or update class game lobby',
                    'security': [{'BearerAuth': []}],
                    'requestBody': {
                        'required': True,
                        'content': {
                            'application/json': {
                                'schema': {
                                    'type': 'object',
                                    'required': ['class_public_id'],
                                    'properties': {
                                        'class_public_id': {'type': 'string', 'format': 'uuid'},
                                        'name': {'type': 'string'},
                                        'ip': {'type': 'string'},
                                        'port': {'type': 'integer'},
                                        'player_count': {'type': 'integer', 'default': 0},
                                    },
                                }
                            }
                        },
                    },
                    'responses': {
                        '200': {'description': 'Lobby updated'},
                        '201': {'description': 'Lobby created'},
                        '400': {'description': 'Validation/integration guidance'},
                        '403': {'description': 'Unauthorized'},
                    },
                }
            },
            '/openapi.json': {
                'get': {
                    'tags': ['Docs'],
                    'summary': 'OpenAPI specification',
                    'responses': {'200': {'description': 'OpenAPI JSON'}},
                }
            },
            '/docs': {
                'get': {
                    'tags': ['Docs'],
                    'summary': 'Swagger UI documentation',
                    'responses': {'200': {'description': 'Swagger UI page'}},
                }
            },
        },
    }


@docs_bp.route('/openapi.json', methods=['GET'])
def openapi_json():
    spec = _openapi_spec(request.host_url)
    return jsonify(spec), 200


@docs_bp.route('/docs', methods=['GET'])
def swagger_docs():
    html = """<!DOCTYPE html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>API Docs</title>
  <link rel=\"stylesheet\" href=\"https://unpkg.com/swagger-ui-dist@5/swagger-ui.css\" />
</head>
<body>
  <div id=\"swagger-ui\"></div>
  <script src=\"https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js\"></script>
  <script>
    window.onload = () => {
      window.ui = SwaggerUIBundle({
        url: '/openapi.json',
        dom_id: '#swagger-ui',
        deepLinking: true,
        persistAuthorization: true,
      });
    };
  </script>
</body>
</html>
"""
    return Response(html, mimetype='text/html')
