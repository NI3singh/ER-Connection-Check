import networkx as nx
from app.models import UserMetadata, EntityLink, SmurfCluster
from sqlalchemy.orm import Session
import logging

logger = logging.getLogger(__name__)

class GraphAnalyzer:
    def __init__(self, db: Session):
        self.db = db
        self.graph = nx.Graph()

    def build_graph_from_db(self):
        """
        Reads ALL metadata from Postgres and builds a NetworkX graph in RAM.
        Nodes = Users, Devices, IPs, CanvasHashes
        Edges = Connections (User <-> Device)
        """
        logger.info("Fetching metadata to build graph...")
        all_metadata = self.db.query(UserMetadata).all()
        
        count = 0
        for row in all_metadata:
            user_node = f"user:{row.user_id}"
            
            # Add User Node
            self.graph.add_node(user_node, type="user")
            
            # Link User <-> Device
            if row.device_hash:
                device_node = f"device:{row.device_hash}"
                self.graph.add_node(device_node, type="device")
                self.graph.add_edge(user_node, device_node, relation="HAS_DEVICE")
            
            # Link User <-> IP
            if row.ip_address:
                ip_node = f"ip:{row.ip_address}"
                self.graph.add_node(ip_node, type="ip")
                self.graph.add_edge(user_node, ip_node, relation="USED_IP")
            
            # Link User <-> Canvas
            if row.canvas_hash:
                canvas_node = f"canvas:{row.canvas_hash}"
                self.graph.add_node(canvas_node, type="canvas")
                self.graph.add_edge(user_node, canvas_node, relation="HAS_CANVAS")
            
            count += 1
            
        logger.info(f"Graph built with {count} metadata records. Nodes: {self.graph.number_of_nodes()}, Edges: {self.graph.number_of_edges()}")

    def find_clusters(self):
        """
        Finds 'Connected Components' (Islands) in the graph.
        Returns a list of clusters, where each cluster is a set of User IDs.
        """
        clusters = []
        
        # networkx algorithm to find unconnected islands
        components = list(nx.connected_components(self.graph))
        
        for component in components:
            # Filter out just the Users from this mixed bag of nodes
            users_in_cluster = [node.split(":")[1] for node in component if node.startswith("user:")]
            
            # We only care if multiple users are linked (Size > 1)
            if len(users_in_cluster) > 1:
                clusters.append(users_in_cluster)
                
        return clusters

    def save_results(self, clusters):
        """
        Writes the detected rings back to Postgres.
        """
        logger.info(f"Found {len(clusters)} suspicious clusters.")
        
        for cluster_users in clusters:
            # 1. Create/Update Smurf Cluster ID
            # For simplicity, we create a name based on the first user (e.g., "Ring-raju_bhai")
            cluster_id = f"Ring-{cluster_users[0]}"
            
            # Calculate Density (GARG-AML Logic Placeholder)
            # For now, density is 1.0 because they are connected.
            risk_score = 0.8 + (len(cluster_users) * 0.05) # More users = Higher Risk
            if risk_score > 1.0: risk_score = 1.0
            
            for user in cluster_users:
                # Check if already exists to avoid duplicates
                existing = self.db.query(SmurfCluster).filter_by(user_id=user, cluster_id=cluster_id).first()
                if not existing:
                    logger.warning(f"🚨 FLAGGING USER: {user} in {cluster_id}")
                    new_record = SmurfCluster(
                        cluster_id=cluster_id,
                        user_id=user,
                        risk_score=risk_score,
                        is_active=True
                    )
                    self.db.add(new_record)
            
            # 2. Save Direct Links (User A <-> User B)
            # This helps the frontend draw lines
            for i in range(len(cluster_users)):
                for j in range(i + 1, len(cluster_users)):
                    user_a = cluster_users[i]
                    user_b = cluster_users[j]
                    
                    # Save Link A -> B
                    link = EntityLink(
                        user_a=user_a,
                        user_b=user_b,
                        link_type="SHARED_METADATA",
                        confidence_score=1.0
                    )
                    self.db.add(link)
        
        self.db.commit()